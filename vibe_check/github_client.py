from __future__ import annotations

import base64
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

from .config import BULK_DUMP_MAX_COMMITS, BULK_DUMP_SIZE_KB, GITHUB_DELAY_S, RunConfig
from .http_util import HttpClient
from .models import Project, RepoAnalysis
from .relevance import find_mentions, name_similarity, relevance_needles

LINK_LAST_RE = re.compile(r'<([^>]+)>;\s*rel="last"', re.I)
PAGE_RE = re.compile(r"[?&]page=(\d+)")
INITIAL_MSG_RE = re.compile(r"^(initial commit|first commit|create repo|created repository)\b", re.I)


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _primary_date(commit_obj: dict) -> datetime | None:
    commit = commit_obj.get("commit") or {}
    dates: list[datetime] = []
    for who in ("author", "committer"):
        raw = (commit.get(who) or {}).get("date")
        if raw:
            dates.append(parse_ts(raw))
    return min(dates) if dates else None


NOREPLY_EMAIL_RE = re.compile(
    r"^(?:\d+\+)?([a-z0-9-]+)@users\.noreply\.github\.com$",
    re.I,
)


def _commit_email(commit_obj: dict) -> str:
    """Prefer real emails; fall back to noreply so we can still see authorship."""
    commit = commit_obj.get("commit") or {}
    noreply = ""
    for who in ("author", "committer"):
        email = ((commit.get(who) or {}).get("email") or "").strip().lower()
        if not email or email == "none@none":
            continue
        if "users.noreply.github.com" in email:
            if not noreply:
                noreply = email
            continue
        return email
    return noreply


def _commit_message(commit_obj: dict) -> str:
    return (((commit_obj.get("commit") or {}).get("message") or "").split("\n")[0])[:70]


def _email_matches_team(author_email: str, team: set[str]) -> bool:
    e = (author_email or "").lower()
    if not e:
        return False
    if e in team:
        return True
    m = NOREPLY_EMAIL_RE.match(e)
    if not m:
        return False
    login = m.group(1).lower()
    for t in team:
        local = t.split("@", 1)[0].lower()
        if local and local == login:
            return True
    return False


class GitHubClient:
    def __init__(self, client: HttpClient, config: RunConfig) -> None:
        self.client = client
        self.config = config
        self.token = os.environ.get("GITHUB_TOKEN", "").strip()
        self.headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "devpost-vibe-check",
        }
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"
        else:
            print(
                "WARNING: GITHUB_TOKEN not set — unauthenticated rate limits are low.",
                flush=True,
            )

    def _get(self, path: str, params: dict | None = None):
        url = f"https://api.github.com{path}"
        for attempt in range(6):
            resp = self.client.get(url, headers=self.headers, params=params)
            remaining = resp.headers.get("X-RateLimit-Remaining")
            retry_after = resp.headers.get("Retry-After")
            body = (resp.text or "")[:300].lower()
            secondary = resp.status_code == 403 and (
                "secondary rate limit" in body or "abuse detection" in body
            )
            primary = resp.status_code == 403 and remaining == "0"
            throttled = resp.status_code == 429 or primary or secondary
            if throttled:
                if retry_after and str(retry_after).isdigit():
                    wait = max(int(retry_after), 1)
                elif primary:
                    reset = int(resp.headers.get("X-RateLimit-Reset", "0"))
                    wait = max(reset - int(time.time()) + 2, 1)
                else:
                    wait = min(60, 2 ** attempt)
                print(
                    f"  GitHub throttled ({resp.status_code}), sleeping {wait}s",
                    flush=True,
                )
                time.sleep(wait)
                continue
            return resp
        return resp

    def count_commits(
        self,
        repo: str,
        sha: str | None = None,
        *,
        until: datetime | None = None,
        since: datetime | None = None,
    ) -> int | None:
        """Commit count via Link: last page (supports since/until filters)."""
        params: dict = {"per_page": 1}
        if sha:
            params["sha"] = sha
        if until is not None:
            params["until"] = until.astimezone(UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        if since is not None:
            params["since"] = since.astimezone(UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        resp = self._get(f"/repos/{repo}/commits", params)
        if resp.status_code != 200:
            return None
        link = resp.headers.get("Link", "")
        m = LINK_LAST_RE.search(link)
        if m:
            page_m = PAGE_RE.search(m.group(1))
            if page_m:
                return int(page_m.group(1))
        data = resp.json()
        return len(data) if isinstance(data, list) else 0

    def list_commits(
        self,
        repo: str,
        *,
        until: datetime | None = None,
        since: datetime | None = None,
        max_pages: int = 10,
    ) -> list[dict]:
        commits: list[dict] = []
        page = 1
        while page <= max_pages:
            params: dict = {"per_page": 100, "page": page}
            if until is not None:
                params["until"] = until.astimezone(UTC).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            if since is not None:
                params["since"] = since.astimezone(UTC).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            resp = self._get(f"/repos/{repo}/commits", params)
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            commits.extend(batch)
            if len(batch) < 100:
                break
            page += 1
            time.sleep(GITHUB_DELAY_S)
        return commits

    def _apply_window_stats(
        self,
        out: RepoAnalysis,
        dated: list[tuple[datetime, str, str]],
    ) -> None:
        """dated = list of (date, message, email)."""
        start = self.config.start
        end = self.config.end
        early = self.config.early_cutoff
        if not dated:
            return

        dated = sorted(dated, key=lambda x: x[0])
        dates = [d for d, _, _ in dated]
        out.earliest_commit_at = dates[0].isoformat()
        out.latest_commit_at = dates[-1].isoformat()
        out.pre_start_commits = sum(1 for d in dates if d < start)
        out.post_end_commits = sum(1 for d in dates if d > end)
        out.in_window_commits = sum(1 for d in dates if start <= d <= end)
        out.outside_window_commits = out.pre_start_commits + out.post_end_commits
        out.early_window_commits = sum(1 for d in dates if d < early)
        out.all_commits_before_start = all(d < start for d in dates)
        out.all_commits_after_end = all(d > end for d in dates)
        out.all_commits_outside_window = all(d < start or d > end for d in dates)
        out.all_commits_too_early = all(d < early for d in dates)

        meaningful = [(d, m, e) for d, m, e in dated if not INITIAL_MSG_RE.match(m or "")]
        if meaningful:
            out.first_meaningful_commit_at = meaningful[0][0].isoformat()
        elif dated:
            out.first_meaningful_commit_at = dated[0][0].isoformat()

        emails: list[str] = []
        seen: set[str] = set()
        for _, _, e in dated:
            if e and e not in seen:
                seen.add(e)
                emails.append(e)
        out.author_emails = emails
        # Keep a wider recent-message window for project/hackathon name matching.
        out.sample_messages = " | ".join(m for _, m, _ in dated[:15] if m)

    def analyze(self, repo: str, team_emails: list[str] | None = None) -> RepoAnalysis:
        out = RepoAnalysis(repo=repo)
        info = self._get(f"/repos/{repo}")
        if info.status_code in {404, 403, 451}:
            out.status = f"HTTP {info.status_code}"
            out.unavailable = True
            return out
        if info.status_code != 200:
            out.status = f"HTTP {info.status_code}"
            out.unavailable = True
            return out

        meta = info.json()
        out.created_at = meta.get("created_at") or ""
        out.is_fork = bool(meta.get("fork"))
        out.default_branch = meta.get("default_branch") or ""
        out.repo_size_kb = int(meta.get("size") or 0)
        parent = meta.get("parent") or {}
        if parent.get("full_name"):
            out.fork_parent = parent["full_name"]
        if out.created_at:
            out.created_before_start = parse_ts(out.created_at) < self.config.start

        total = self.count_commits(repo, sha=out.default_branch or None)
        out.total_commits = total
        time.sleep(GITHUB_DELAY_S)

        max_pages = 1 if (total is not None and total <= 100) else 5
        sample = self.list_commits(repo, max_pages=max_pages)
        if not sample and total == 0:
            return out
        if not sample and (total is None or total > 0):
            # Repo metadata ok but commit list empty → do not score as "clean".
            out.status = "commits fetch failed"
            out.window_stats_incomplete = True
            out.readme_excerpt = self._readme_text(repo)
            return out

        dated: list[tuple[datetime, str, str]] = []
        for c in sample:
            d = _primary_date(c)
            if d is None:
                continue
            dated.append((d, _commit_message(c), _commit_email(c)))

        self._apply_window_stats(out, dated)

        if total is not None and total <= len(dated):
            out.total_commits = len(dated)
        elif total is not None and total > len(dated):
            # Use Link-header counts (not capped page walks) for pre/post.
            pre_n = self.count_commits(
                repo, sha=out.default_branch or None, until=self.config.start
            )
            post_n = self.count_commits(
                repo, sha=out.default_branch or None, since=self.config.end
            )
            time.sleep(GITHUB_DELAY_S)
            if pre_n is None or post_n is None:
                out.window_stats_incomplete = True
                # Fall back to capped samples but do not invent full in-window.
                pre_full = self.list_commits(repo, until=self.config.start, max_pages=5)
                post_full = self.list_commits(repo, since=self.config.end, max_pages=5)
                out.pre_start_commits = sum(
                    1
                    for c in pre_full
                    if (d := _primary_date(c)) and d < self.config.start
                )
                out.post_end_commits = sum(
                    1
                    for c in post_full
                    if (d := _primary_date(c)) and d > self.config.end
                )
                if len(pre_full) >= 500 or len(post_full) >= 500:
                    out.window_stats_incomplete = True
                out.outside_window_commits = out.pre_start_commits + out.post_end_commits
                # Conservative: only claim in-window when we can bound outside.
                if not out.window_stats_incomplete:
                    out.in_window_commits = max(total - out.outside_window_commits, 0)
                else:
                    out.in_window_commits = max(
                        0, min(out.in_window_commits, total - out.outside_window_commits)
                    )
            else:
                out.pre_start_commits = pre_n
                out.post_end_commits = post_n
                out.outside_window_commits = pre_n + post_n
                out.in_window_commits = max(total - out.outside_window_commits, 0)

            if out.pre_start_commits >= total:
                out.all_commits_before_start = True
                out.all_commits_outside_window = True
                out.all_commits_too_early = True
            if out.post_end_commits >= total:
                out.all_commits_after_end = True
                out.all_commits_outside_window = True
            if out.outside_window_commits >= total:
                out.all_commits_outside_window = True

        # Bulk dump: tiny history + fat tree (GitHub size is KB of git content).
        tc = out.total_commits if out.total_commits is not None else 0
        if tc <= BULK_DUMP_MAX_COMMITS and out.repo_size_kb >= BULK_DUMP_SIZE_KB:
            out.bulk_dump_suspect = True

        if team_emails:
            team = {e.lower() for e in team_emails}
            out.email_overlap = sum(
                1 for e in out.author_emails if _email_matches_team(e, team)
            )

        # README once per repo — used later for project/hackathon name hits.
        out.readme_excerpt = self._readme_text(repo)
        time.sleep(GITHUB_DELAY_S)
        return out

    def _readme_text(self, repo: str) -> str:
        resp = self._get(f"/repos/{repo}/readme")
        if resp.status_code != 200:
            return ""
        data = resp.json()
        content = data.get("content") or ""
        encoding = (data.get("encoding") or "").lower()
        try:
            if encoding == "base64":
                raw = base64.b64decode(content)
                return raw.decode("utf-8", errors="replace")[:8000]
            return str(content)[:8000]
        except Exception:
            return ""

    def apply_relevance(self, analysis: RepoAnalysis, project: Project) -> RepoAnalysis:
        """
        Check whether project / hackathon name shows up in repo name, recent
        commit messages, or README (common for real hackathon submissions).
        Local-only — README/commits already fetched in analyze().
        """
        if analysis.unavailable:
            return analysis

        needles = relevance_needles(project, self.config.hackathon_url)
        hits: list[str] = []
        sources: list[str] = []

        sim = name_similarity(analysis.repo, project)
        analysis.name_match_score = sim
        if sim >= 0.6:
            sources.append("repo_name")
            title_hits = find_mentions(analysis.repo.split("/")[-1], needles)
            for h in title_hits or needles[:1]:
                if h not in hits:
                    hits.append(h)

        msg_blob = analysis.sample_messages or ""
        msg_hits = find_mentions(msg_blob, needles)
        if msg_hits:
            sources.append("commits")
            for h in msg_hits:
                if h not in hits:
                    hits.append(h)

        readme_hits = find_mentions(analysis.readme_excerpt or "", needles)
        if readme_hits:
            sources.append("readme")
            for h in readme_hits:
                if h not in hits:
                    hits.append(h)

        analysis.relevance_hits = hits
        analysis.relevance_sources = sources
        return analysis

    def discover_repos_for_project(self, project: Project) -> list[str]:
        """
        From GitHub user logins on the project, find repos that look like this
        submission (name match and/or pushed during the hackathon window).
        """
        logins = list(project.github_users)
        # Also treat repo owners as users to scan.
        for repo in project.github_repos:
            owner = repo.split("/")[0] if "/" in repo else ""
            if owner and owner.lower() not in {u.lower() for u in logins}:
                logins.append(owner)

        if not logins:
            return []

        start = self.config.start - timedelta(days=1)
        end = self.config.end + timedelta(days=2)
        found: list[str] = []
        seen = {r.lower() for r in project.github_repos}

        for login in logins:
            resp = self._get(
                f"/users/{login}/repos",
                {"sort": "pushed", "per_page": 30, "type": "owner"},
            )
            time.sleep(GITHUB_DELAY_S)
            if resp.status_code != 200:
                continue
            repos = resp.json()
            if not isinstance(repos, list):
                continue
            for meta in repos:
                full = meta.get("full_name") or ""
                if not full or full.lower() in seen:
                    continue
                name = meta.get("name") or full.split("/")[-1]
                sim = name_similarity(full, project)
                pushed_raw = meta.get("pushed_at") or ""
                in_window = False
                if pushed_raw:
                    try:
                        pushed = parse_ts(pushed_raw)
                        in_window = start <= pushed <= end
                    except Exception:
                        in_window = False
                # Accept strong name match, or moderate name + window activity,
                # or exact-ish title compact match via needles in name.
                from .relevance import hackathon_tokens, project_tokens

                proj_needles = [t for t in project_tokens(project) if len(t) >= 5]
                hack_needles = [t for t in hackathon_tokens(self.config.hackathon_url) if len(t) >= 5]
                title_hits = find_mentions(name, proj_needles)
                hack_hits = find_mentions(name, hack_needles)
                take = False
                if sim >= 0.85:
                    take = True
                elif title_hits and len(max(title_hits, key=len)) >= 5 and (
                    in_window or sim >= 0.6
                ):
                    take = True
                elif (
                    hack_hits
                    and in_window
                    and not project.github_repos
                    and len(max(hack_hits, key=len)) >= 6
                ):
                    # Only when CSV/Devpost gave zero repos — avoid stacking
                    # every teammate's "*-stormhacks*" portfolio repo.
                    take = True
                if take:
                    seen.add(full.lower())
                    found.append(full)
        return found

    def discover_many(
        self, projects: list[Project], *, workers: int = 8
    ) -> int:
        """Attach discovered repos onto projects. Returns # new repo links."""
        need = [p for p in projects if p.github_users or p.github_repos]
        if not need:
            return 0
        workers = max(1, min(workers, len(need)))
        added = 0

        def _job(proj: Project) -> tuple[str, list[str]]:
            local = GitHubClient(HttpClient(), self.config)
            try:
                return proj.slug, local.discover_repos_for_project(proj)
            except Exception as exc:
                print(f"  discover failed [{proj.slug}]: {exc}", flush=True)
                return proj.slug, []

        by_slug = {p.slug: p for p in need}
        if workers == 1:
            for p in need:
                repos = self.discover_repos_for_project(p)
                before = {r.lower() for r in p.github_repos}
                for r in repos:
                    if r.lower() not in before:
                        p.github_repos.append(r)
                        added += 1
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(_job, p) for p in need]
                for fut in as_completed(futs):
                    slug, repos = fut.result()
                    p = by_slug.get(slug)
                    if not p:
                        continue
                    before = {r.lower() for r in p.github_repos}
                    for r in repos:
                        if r.lower() not in before:
                            p.github_repos.append(r)
                            added += 1
        return added

    def analyze_many(
        self,
        repos: list[str],
        *,
        team_emails: list[str] | None = None,
        workers: int = 1,
    ) -> dict[str, RepoAnalysis]:
        if not repos:
            return {}
        workers = max(1, workers)

        from rich.progress import (
            BarColumn,
            Progress,
            TaskProgressColumn,
            TextColumn,
            TimeElapsedColumn,
            TimeRemainingColumn,
        )

        def _job(repo: str) -> tuple[str, RepoAnalysis]:
            # Each worker gets its own HTTP session (requests.Session is not thread-safe).
            local = GitHubClient(HttpClient(), self.config)
            try:
                return repo, local.analyze(repo, team_emails=team_emails)
            except Exception as exc:
                return repo, RepoAnalysis(
                    repo=repo, status=f"error: {exc}", unavailable=True
                )

        out: dict[str, RepoAnalysis] = {}
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            transient=True,
        ) as progress:
            task_id = progress.add_task("GitHub analyze", total=len(repos))
            if workers == 1 or len(repos) == 1:
                for r in repos:
                    out[r] = self.analyze(r, team_emails=team_emails)
                    progress.advance(task_id)
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = [pool.submit(_job, r) for r in repos]
                    for fut in as_completed(futs):
                        repo, analysis = fut.result()
                        out[repo] = analysis
                        progress.advance(task_id)
        return out

    def analyze_projects(
        self,
        projects: list[Project],
        *,
        workers: int = 8,
    ) -> dict[str, dict[str, RepoAnalysis]]:
        """
        Analyze unique repos once, then apply per-project relevance.
        Returns {project.slug: {repo: RepoAnalysis}}.
        """
        repo_jobs: list[str] = []
        for p in projects:
            for repo in p.github_repos:
                repo_jobs.append(repo)
        unique = list(dict.fromkeys(repo_jobs))
        base = self.analyze_many(unique, workers=workers)

        # Relevance is project-specific (copy analysis per project).
        from copy import deepcopy

        out: dict[str, dict[str, RepoAnalysis]] = {}
        for p in projects:
            out[p.slug] = {}
            for repo in p.github_repos:
                src = base.get(repo)
                if src is None:
                    continue
                analysis = deepcopy(src)
                try:
                    self.apply_relevance(analysis, p)
                except Exception as exc:
                    print(f"  relevance failed [{repo}]: {exc}", flush=True)
                out[p.slug][repo] = analysis
        return out
