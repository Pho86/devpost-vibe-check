from __future__ import annotations

import base64
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

from .config import BULK_DUMP_MAX_COMMITS, BULK_DUMP_SIZE_KB, GITHUB_DELAY_S, RunConfig
from .http_util import HttpClient
from .models import Project, RepoAnalysis
from .relevance import find_mentions, name_similarity, relevance_needles

LINK_LAST_RE = re.compile(r'<([^>]+)>;\s*rel="last"', re.I)
PAGE_RE = re.compile(r"[?&]page=(\d+)")
INITIAL_MSG_RE = re.compile(
    r"^(initial commit|first commit|create repo|created repository)\b", re.I
)

# Shared across worker GitHubClient instances so parallel analyze does not burst.
_GITHUB_GATE = threading.Lock()
_GITHUB_NEXT_OK = 0.0


def _throttle_github() -> None:
    """Space GitHub API calls globally (all threads / client instances)."""
    global _GITHUB_NEXT_OK
    with _GITHUB_GATE:
        now = time.monotonic()
        wait = _GITHUB_NEXT_OK - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _GITHUB_NEXT_OK = now + GITHUB_DELAY_S


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

    def _repo_reachable(self, repo: str) -> bool:
        """True when /repos/{repo} looks usable (not 404/private/blocked)."""
        resp = self._get(f"/repos/{repo}")
        return resp.status_code == 200

    def _get(self, path: str, params: dict | None = None):
        url = f"https://api.github.com{path}"
        for attempt in range(6):
            _throttle_github()
            # tries=1: GitHubClient owns 429/abuse backoff; avoid double-sleep with HttpClient.
            resp = self.client.get(url, headers=self.headers, params=params, tries=1)
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
                    wait = min(60, 2**attempt)
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
            params["until"] = until.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if since is not None:
            params["since"] = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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
                params["until"] = until.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if since is not None:
                params["since"] = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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
        return commits

    def _list_oldest_commits(
        self,
        repo: str,
        *,
        sha: str | None = None,
        max_commits: int = 50,
    ) -> list[dict]:
        """
        Fetch the oldest commits (GitHub returns newest-first; last page is oldest).
        Used when the recent sample misses early history on large repos.
        """
        total = self.count_commits(repo, sha=sha)
        if not total:
            return []
        per_page = 100
        last_page = max(1, (total + per_page - 1) // per_page)
        collected: list[dict] = []
        page = last_page
        while page >= 1 and len(collected) < max_commits:
            params: dict = {"per_page": per_page, "page": page}
            if sha:
                params["sha"] = sha
            resp = self._get(f"/repos/{repo}/commits", params)
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            # batch is newest→oldest within the page; prepend so collected stays
            # chronological oldest-first after reverse at the end.
            collected = batch + collected
            if page == last_page and len(batch) < per_page:
                break
            page -= 1
        # Oldest first, capped.
        dated_sorted = sorted(
            (c for c in collected if _primary_date(c) is not None),
            key=lambda c: _primary_date(c) or datetime.min.replace(tzinfo=UTC),
        )
        return dated_sorted[:max_commits]

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
        # Newest messages first — better for project/hackathon name matching.
        recent = list(reversed(dated[-15:]))
        out.sample_messages = " | ".join(m for _, m, _ in recent if m)

    def _refine_large_repo_window(
        self,
        out: RepoAnalysis,
        repo: str,
        *,
        total: int,
        sample_dated: list[tuple[datetime, str, str]],
    ) -> None:
        """
        Recent samples miss early history on large repos. Reconcile counts via
        Link-header filters and pull the oldest page for first-commit signals.
        """
        sha = out.default_branch or None
        pre_n = self.count_commits(repo, sha=sha, until=self.config.start)
        post_n = self.count_commits(repo, sha=sha, since=self.config.end)
        early_n = self.count_commits(repo, sha=sha, until=self.config.early_cutoff)

        if pre_n is None or post_n is None:
            out.window_stats_incomplete = True
            pre_full = self.list_commits(repo, until=self.config.start, max_pages=5)
            post_full = self.list_commits(repo, since=self.config.end, max_pages=5)
            out.pre_start_commits = sum(
                1 for c in pre_full if (d := _primary_date(c)) and d < self.config.start
            )
            out.post_end_commits = sum(
                1 for c in post_full if (d := _primary_date(c)) and d > self.config.end
            )
            if len(pre_full) >= 500 or len(post_full) >= 500:
                out.window_stats_incomplete = True
            out.outside_window_commits = out.pre_start_commits + out.post_end_commits
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

        if early_n is not None:
            out.early_window_commits = early_n

        if out.pre_start_commits >= total:
            out.all_commits_before_start = True
            out.all_commits_outside_window = True
            out.all_commits_too_early = True
        else:
            out.all_commits_before_start = False
            if out.pre_start_commits == 0 and out.post_end_commits == 0:
                out.all_commits_outside_window = False
            out.all_commits_too_early = bool(early_n is not None and early_n >= total and total > 0)
        if out.post_end_commits >= total:
            out.all_commits_after_end = True
            out.all_commits_outside_window = True
        else:
            out.all_commits_after_end = False
        if out.outside_window_commits >= total:
            out.all_commits_outside_window = True

        # Oldest page → true earliest / first meaningful (sample is newest-first).
        oldest = self._list_oldest_commits(repo, sha=sha, max_commits=50)
        oldest_dated: list[tuple[datetime, str, str]] = []
        for c in oldest:
            d = _primary_date(c)
            if d is None:
                continue
            oldest_dated.append((d, _commit_message(c), _commit_email(c)))
        if oldest_dated:
            oldest_dated.sort(key=lambda x: x[0])
            out.earliest_commit_at = oldest_dated[0][0].isoformat()
            meaningful = [
                (d, m, e) for d, m, e in oldest_dated if not INITIAL_MSG_RE.match(m or "")
            ]
            if meaningful:
                out.first_meaningful_commit_at = meaningful[0][0].isoformat()
            else:
                out.first_meaningful_commit_at = oldest_dated[0][0].isoformat()
            # Merge author emails from oldest + recent sample.
            seen = set(out.author_emails)
            for _, _, e in oldest_dated:
                if e and e not in seen:
                    seen.add(e)
                    out.author_emails.append(e)
        elif sample_dated:
            # Keep sample-derived earliest as a lower bound (may be too recent).
            out.window_stats_incomplete = True

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
            self._refine_large_repo_window(out, repo, total=total, sample_dated=dated)

        # Bulk dump: tiny history + fat tree (GitHub size is KB of git content).
        tc = out.total_commits if out.total_commits is not None else 0
        if tc <= BULK_DUMP_MAX_COMMITS and out.repo_size_kb >= BULK_DUMP_SIZE_KB:
            out.bulk_dump_suspect = True

        if team_emails:
            team = {e.lower() for e in team_emails}
            out.email_overlap = sum(1 for e in out.author_emails if _email_matches_team(e, team))

        # README once per repo — used later for project/hackathon name hits.
        out.readme_excerpt = self._readme_text(repo)
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
        # Always scan the repo short name for project/hackathon needles.
        # Title↔repo similarity alone is not required — short Devpost titles
        # like "OK" never match StormsHacks2026 even when the hackathon name
        # is clearly in the repo.
        repo_short = analysis.repo.split("/")[-1]
        repo_hits = find_mentions(repo_short, needles)
        if repo_hits:
            sources.append("repo_name")
            for h in repo_hits:
                if h not in hits:
                    hits.append(h)
        elif sim >= 0.6:
            sources.append("repo_name")
            for h in needles[:1]:
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

        If Devpost/export already linked a reachable repo, skip profile discovery —
        teammates often have empty KeyNote stubs that would otherwise outrank the
        real link. Dead/private Devpost links still fall through so we can find
        the real teammate repo.
        """
        linked = list(
            dict.fromkeys([*(project.devpost_github_repos or []), *(project.github_repos or [])])
        )
        if linked and any(self._repo_reachable(r) for r in linked):
            return []

        logins = list(project.github_users)
        # Also treat repo owners as users to scan.
        for repo in project.github_repos:
            owner = repo.split("/")[0] if "/" in repo else ""
            if owner and owner.lower() not in {u.lower() for u in logins}:
                logins.append(owner)
        for profile in project.team_profiles:
            if profile and profile.lower() not in {u.lower() for u in logins}:
                logins.append(profile)

        if not logins:
            return []

        start = self.config.start - timedelta(days=1)
        end = self.config.end + timedelta(days=2)
        seen = {r.lower() for r in project.github_repos}
        from .relevance import hackathon_tokens, project_tokens

        proj_needles = [t for t in project_tokens(project) if len(t) >= 5]
        hack_needles = [t for t in hackathon_tokens(self.config.hackathon_url) if len(t) >= 5]

        # Pass 1: project-name matches (incl. collaborator repos via type=all).
        # Pass 2: only if still empty, allow one hackathon-named repo in-window.
        name_hits: list[str] = []
        hack_fallback: list[str] = []

        for login in logins:
            # type=all: include collaborator repos (common when one teammate owns
            # the submission and others only appear as collaborators).
            resp = self._get(
                f"/users/{login}/repos",
                {"sort": "pushed", "per_page": 50, "type": "all"},
            )
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
                title_hits = find_mentions(name, proj_needles)
                hack_hits = find_mentions(name, hack_needles)
                if sim >= 0.85 or (
                    title_hits and len(max(title_hits, key=len)) >= 5 and (in_window or sim >= 0.6)
                ):
                    seen.add(full.lower())
                    name_hits.append(full)
                elif (
                    not project.github_repos
                    and hack_hits
                    and in_window
                    and len(max(hack_hits, key=len)) >= 6
                ):
                    hack_fallback.append(full)

        if name_hits:
            return name_hits
        # No project-name match anywhere — keep a single hackathon-named candidate
        # so zero-link submissions still get a repo to score.
        if not project.github_repos and hack_fallback:
            return [hack_fallback[0]]
        return []

    def discover_many(self, projects: list[Project], *, workers: int = 8) -> int:
        """Attach discovered repos onto projects. Returns # new repo links."""
        # Discover when there is no reachable Devpost/export link yet (dead links OK).
        need = [p for p in projects if p.github_users or p.team_profiles]
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
                return repo, RepoAnalysis(repo=repo, status=f"error: {exc}", unavailable=True)

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
