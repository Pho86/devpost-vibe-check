from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

from .config import DEVPOST_DELAY_S, SKIP_GITHUB_OWNERS, SKIP_GITHUB_REPOS, RunConfig
from .http_util import HttpClient
from .models import Project
from .relevance import SKIP_DEVPOST_USERS

# http(s)://github.com/a/b  |  github.com/a/b  |  git@github.com:a/b.git
REPO_RE = re.compile(
    r"(?:https?://(?:www\.)?|git@)?github\.com[:/]([\w.-]+)/([\w.-]+)",
    re.I,
)
# github.com/username (no repo) — useful from Devpost profile "Website" links
GH_USER_RE = re.compile(
    r"(?:https?://(?:www\.)?)?github\.com/([\w.-]+)/?(?:[?#\"'\s]|$)",
    re.I,
)
DEVPOST_USER_RE = re.compile(
    r"https?://(?:www\.)?devpost\.com/([A-Za-z0-9_-]+)/?(?=[\"'\s<>?#]|$)",
    re.I,
)
SOFTWARE_HREF_RE = re.compile(
    r"https?://(?:www\.)?devpost\.com/software/([a-zA-Z0-9_-]+)",
    re.I,
)
SOFTWARE_PATH_RE = re.compile(r"/software/([a-zA-Z0-9_-]+)", re.I)
# Submission URLs look like /submissions/1213947-crimepath — not valid software slugs.
SUBMISSION_SLUG_RE = re.compile(r"^\d{5,}-", re.I)
COUNT_RE = re.compile(r"(\d+)\s*[–-]\s*(\d+)\s+of\s+(\d+)", re.I)
HACKATHON_HOST_RE = re.compile(
    r"https?://([a-z0-9-]+)\.devpost\.com",
    re.I,
)
SKIP_SOFTWARE_SLUGS = {
    "built-with",
    "search",
    "new",
    "edit",
    "preview",
    "likes",
    "comments",
    "",
}


def _valid_software_slug(slug: str) -> bool:
    s = (slug or "").strip().lower()
    if not s or s in SKIP_SOFTWARE_SLUGS:
        return False
    if SUBMISSION_SLUG_RE.match(s):
        return False
    return True


def extract_repos(text: str) -> list[str]:
    repos: list[str] = []
    seen: set[str] = set()
    for owner, name in REPO_RE.findall(text or ""):
        name = re.sub(r"(\.git)$", "", name, flags=re.I)
        name = name.rstrip(".,);]'\"")
        if owner.lower() in SKIP_GITHUB_OWNERS:
            continue
        if name.lower() in SKIP_GITHUB_REPOS:
            continue
        if len(owner) < 2 or len(name) < 2:
            continue
        key = f"{owner}/{name}"
        low = key.lower()
        if low not in seen:
            seen.add(low)
            repos.append(key)
    return repos


def extract_github_users(text: str) -> list[str]:
    users: list[str] = []
    seen: set[str] = set()
    for owner, _name in REPO_RE.findall(text or ""):
        low = owner.lower()
        if low in SKIP_GITHUB_OWNERS or low in seen or len(owner) < 2:
            continue
        seen.add(low)
        users.append(owner)
    for m in GH_USER_RE.finditer(text or ""):
        owner = m.group(1).rstrip(".,);]'\"")
        low = owner.lower()
        if low in SKIP_GITHUB_OWNERS or low in SKIP_GITHUB_REPOS:
            continue
        if low in seen or len(owner) < 2:
            continue
        seen.add(low)
        users.append(owner)
    return users


def extract_devpost_users(text: str) -> list[str]:
    users: list[str] = []
    seen: set[str] = set()
    for m in DEVPOST_USER_RE.finditer(text or ""):
        slug = m.group(1)
        low = slug.lower()
        if low in SKIP_DEVPOST_USERS or low in seen:
            continue
        if len(slug) < 2:
            continue
        seen.add(low)
        users.append(slug)
    return users


def _merge_unique(dst: list[str], extras: list[str]) -> None:
    seen = {x.lower() for x in dst}
    for x in extras:
        low = x.lower()
        if low not in seen:
            seen.add(low)
            dst.append(x)


def guess_hackathon_url(projects: list[Project]) -> str | None:
    for p in projects:
        m = HACKATHON_HOST_RE.match(p.url or "")
        if m:
            return f"https://{m.group(1)}.devpost.com"
    return None


def _normalize_title(title: str) -> str:
    t = (title or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


class DevpostScraper:
    def __init__(self, client: HttpClient, config: RunConfig) -> None:
        self.client = client
        self.config = config

    def fetch_gallery_projects(self) -> list[Project]:
        projects: list[Project] = []
        seen_slugs: set[str] = set()
        page = 1
        total: int | None = None

        while True:
            if self.config.max_pages is not None and page > self.config.max_pages:
                break

            url = f"{self.config.gallery_url}?page={page}"
            print(f"[gallery] page {page}: {url}", flush=True)
            resp = self.client.get(url)
            if resp.status_code != 200:
                raise RuntimeError(f"Gallery HTTP {resp.status_code} for {url}")

            html = resp.text
            page_projects = self._parse_gallery_page(html)
            if not page_projects:
                print(f"[gallery] no projects on page {page}, stopping", flush=True)
                break

            added = 0
            for proj in page_projects:
                if proj.slug in seen_slugs:
                    continue
                seen_slugs.add(proj.slug)
                projects.append(proj)
                added += 1

            m = COUNT_RE.search(
                BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
            )
            if m:
                total = int(m.group(3))
                print(
                    f"[gallery] parsed {added} new ({len(projects)} total"
                    + (f" / {total}" if total else "")
                    + ")",
                    flush=True,
                )
            else:
                print(f"[gallery] parsed {added} new ({len(projects)} total)", flush=True)

            if self.config.limit is not None and len(projects) >= self.config.limit:
                projects = projects[: self.config.limit]
                break
            if total is not None and len(projects) >= total:
                break
            if added == 0:
                break

            page += 1
            time.sleep(DEVPOST_DELAY_S)

        return projects

    def build_title_software_map(self) -> dict[str, str]:
        """Map normalized project title -> https://devpost.com/software/<slug>."""
        title_map: dict[str, str] = {}
        page = 1
        total: int | None = None
        while True:
            if self.config.max_pages is not None and page > self.config.max_pages:
                break
            url = f"{self.config.gallery_url}?page={page}"
            print(f"[gallery-resolve] page {page}", flush=True)
            resp = self.client.get(url)
            if resp.status_code != 200:
                print(f"[gallery-resolve] HTTP {resp.status_code}, stopping", flush=True)
                break
            html = resp.text
            soup = BeautifulSoup(html, "html.parser")

            for block in soup.select(".gallery-item, .software-entry, li.software"):
                link = block.select_one('a[href*="/software/"]')
                if not link:
                    continue
                href = link.get("href") or ""
                m = SOFTWARE_HREF_RE.search(href) or SOFTWARE_PATH_RE.search(href)
                if not m:
                    continue
                slug = m.group(1)
                if not _valid_software_slug(slug):
                    continue
                title_el = block.select_one("h5, h3, .software-name, .entry-title")
                title = _normalize_title(
                    title_el.get_text(" ", strip=True) if title_el else ""
                )
                soft = f"https://devpost.com/software/{slug}"
                if title:
                    title_map.setdefault(title, soft)

            # Fallback: bare software links with nearby text
            for a in soup.select('a[href*="/software/"]'):
                href = a.get("href") or ""
                m = SOFTWARE_HREF_RE.search(href) or SOFTWARE_PATH_RE.search(href)
                if not m or not _valid_software_slug(m.group(1)):
                    continue
                slug = m.group(1)
                text = _normalize_title(a.get_text(" ", strip=True))
                if text and text != slug.replace("-", " "):
                    title_map.setdefault(text, f"https://devpost.com/software/{slug}")

            m = COUNT_RE.search(soup.get_text(" ", strip=True))
            if m:
                total = int(m.group(3))
            if total is not None and len(title_map) >= total:
                break
            # stop if page looked empty of software links
            if not SOFTWARE_PATH_RE.search(html) and not SOFTWARE_HREF_RE.search(html):
                break
            page += 1
            if page > 40:
                break
            time.sleep(DEVPOST_DELAY_S)

        print(f"[gallery-resolve] mapped {len(title_map)} titles", flush=True)
        return title_map

    def resolve_missing_urls(self, projects: list[Project]) -> int:
        """
        Fill blank / csv:// submission URLs using the public gallery title map,
        then a best-effort /software/<slug> guess.
        """
        missing = [
            p
            for p in projects
            if not (p.url or "").startswith("http")
            and p.slug
            and p.slug.lower() != "untitled"
        ]
        if not missing:
            return 0

        if not self.config.hackathon_url or self.config.hackathon_url == "csv-import":
            print(
                "[gallery-resolve] skip — set --hackathon so empty Submission Url "
                "rows can be matched via the project gallery",
                flush=True,
            )
            return 0

        title_map = self.build_title_software_map()
        fixed = 0
        for p in missing:
            key = _normalize_title(p.title)
            url = title_map.get(key)
            if not url and p.slug and _valid_software_slug(p.slug):
                url = f"https://devpost.com/software/{p.slug}"
            if url:
                p.url = url
                fixed += 1
        print(f"[gallery-resolve] filled {fixed} missing submission URLs", flush=True)
        return fixed

    def _parse_gallery_page(self, html: str) -> list[Project]:
        soup = BeautifulSoup(html, "html.parser")
        projects: list[Project] = []
        seen: set[str] = set()

        for block in soup.select(".gallery-item, .software-entry, li.software"):
            link = block.select_one('a[href*="/software/"]')
            if not link:
                continue
            href = link.get("href") or ""
            m = SOFTWARE_HREF_RE.search(href) or SOFTWARE_PATH_RE.search(href)
            if not m:
                continue
            slug = m.group(1)
            if not _valid_software_slug(slug) or slug in seen:
                continue
            seen.add(slug)
            title_el = block.select_one("h5, h3, .software-name, .entry-title")
            tagline_el = block.select_one("p, .tagline, .small")
            title = (title_el.get_text(" ", strip=True) if title_el else slug).strip()
            tagline = tagline_el.get_text(" ", strip=True) if tagline_el else ""
            projects.append(
                Project(
                    title=title or slug,
                    url=f"https://devpost.com/software/{slug}",
                    slug=slug,
                    tagline=tagline,
                )
            )

        if projects:
            return projects

        for m in SOFTWARE_HREF_RE.finditer(html):
            slug = m.group(1)
            if not _valid_software_slug(slug) or slug in seen:
                continue
            seen.add(slug)
            projects.append(
                Project(
                    title=slug.replace("-", " "),
                    url=f"https://devpost.com/software/{slug}",
                    slug=slug,
                )
            )
        return projects

    def _candidate_pages(self, project: Project) -> list[str]:
        urls: list[str] = []
        if (project.url or "").startswith("http"):
            urls.append(project.url)
        # Only guess /software/<slug> when slug looks like a real software slug
        # (not a numeric submission id like 1213947-crimepath).
        if project.slug and _valid_software_slug(project.slug):
            soft = f"https://devpost.com/software/{project.slug}"
            if soft not in urls:
                urls.append(soft)
        return urls

    def enrich_project(self, project: Project) -> Project:
        """Fetch submission (+ software if needed): GitHub repos + team profiles."""
        found: list[str] = list(project.github_repos)
        seen = {r.lower() for r in found}
        profiles: list[str] = list(project.team_profiles)
        gh_users: list[str] = list(project.github_users)
        pages = self._candidate_pages(project)
        followed_software = False

        for url in pages:
            try:
                resp = self.client.get(url)
            except Exception as exc:
                print(f"  scrape error {url}: {exc}", flush=True)
                continue
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            title_el = soup.select_one("#app-title, h1")
            if title_el and (not project.title or project.title.lower() == "untitled"):
                project.title = title_el.get_text(" ", strip=True) or project.title

            blobs = [resp.text]
            for a in soup.select("a[href*='github.com'], a[href*='Github.com']"):
                blobs.append(a.get("href") or "")

            for blob in blobs:
                for repo in extract_repos(blob):
                    low = repo.lower()
                    if low not in seen:
                        seen.add(low)
                        found.append(repo)
                _merge_unique(gh_users, extract_github_users(blob))

            _merge_unique(profiles, extract_devpost_users(resp.text))
            # Anchor hrefs are more reliable than a full-HTML regex.
            for a in soup.select("a[href*='devpost.com/']"):
                href = a.get("href") or ""
                _merge_unique(profiles, extract_devpost_users(href))

            # Chase software canonical page once if still no repos (gallery closed).
            if not found and not followed_software:
                for a in soup.select('a[href*="/software/"]'):
                    href = a.get("href") or ""
                    m = SOFTWARE_HREF_RE.search(href) or SOFTWARE_PATH_RE.search(href)
                    if not m or not _valid_software_slug(m.group(1)):
                        continue
                    soft = f"https://devpost.com/software/{m.group(1)}"
                    if soft not in pages:
                        pages.append(soft)
                        followed_software = True
                        break

            time.sleep(DEVPOST_DELAY_S)
            # Keep going if we still need team profiles (don't stop at first repo).
            if found and profiles:
                break

        project.github_repos = found
        project.team_profiles = profiles
        project.github_users = gh_users
        if not (project.url or "").startswith("http"):
            for u in pages:
                if "/software/" in u:
                    project.url = u
                    break
        return project

    def enrich_user_profile(self, username: str) -> tuple[list[str], list[str]]:
        """
        Scrape https://devpost.com/<user> for GitHub user + repo links.
        Returns (repos, github_usernames).
        """
        url = f"https://devpost.com/{username}"
        try:
            resp = self.client.get(url)
        except Exception as exc:
            print(f"  user scrape error {url}: {exc}", flush=True)
            return [], []
        if resp.status_code != 200:
            return [], []
        repos = extract_repos(resp.text)
        users = extract_github_users(resp.text)
        # Profile "Website" is often github.com/<login> with no repo.
        if not users and not repos:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.select("a[href*='github.com']"):
                href = a.get("href") or ""
                repos.extend(extract_repos(href))
                users.extend(extract_github_users(href))
        # Dedupe
        seen_r: set[str] = set()
        uniq_repos: list[str] = []
        for r in repos:
            low = r.lower()
            if low not in seen_r:
                seen_r.add(low)
                uniq_repos.append(r)
        seen_u: set[str] = set()
        uniq_users: list[str] = []
        for u in users:
            low = u.lower()
            if low not in seen_u:
                seen_u.add(low)
                uniq_users.append(u)
        time.sleep(DEVPOST_DELAY_S)
        return uniq_repos, uniq_users

    def enrich_many(self, projects: list[Project], *, workers: int = 8) -> None:
        """Parallel Devpost submission scrapes (each worker has its own HTTP session)."""
        if not projects:
            return
        workers = max(1, min(workers, len(projects)))

        from rich.progress import (
            BarColumn,
            Progress,
            TaskProgressColumn,
            TextColumn,
            TimeElapsedColumn,
            TimeRemainingColumn,
        )

        def _job(proj: Project) -> Project:
            local = DevpostScraper(HttpClient(), self.config)
            return local.enrich_project(proj)

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            transient=True,
        ) as progress:
            task_id = progress.add_task("Devpost scrape", total=len(projects))
            if workers == 1:
                for p in projects:
                    self.enrich_project(p)
                    progress.advance(task_id)
                return

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {pool.submit(_job, p): p for p in projects}
                for fut in as_completed(futs):
                    original = futs[fut]
                    try:
                        enriched = fut.result()
                        original.github_repos = enriched.github_repos
                        original.github_users = enriched.github_users
                        original.team_profiles = enriched.team_profiles
                        original.url = enriched.url or original.url
                        original.title = enriched.title or original.title
                        original.tagline = enriched.tagline or original.tagline
                    except Exception as exc:
                        print(f"  scrape failed [{original.slug}]: {exc}", flush=True)
                    progress.advance(task_id)

    def scrape_team_users(
        self, projects: list[Project], *, workers: int = 8
    ) -> None:
        """
        Stage: scrape each unique Devpost team profile, merge GitHub users/repos
        back onto every project that listed that profile.
        """
        # Preserve first-seen casing for profile URLs.
        seen_lower: set[str] = set()
        usernames: list[str] = []
        for p in projects:
            for u in p.team_profiles:
                low = u.lower()
                if low not in seen_lower:
                    seen_lower.add(low)
                    usernames.append(u)
        if not usernames:
            print("[users] no Devpost team profiles found", flush=True)
            return

        print(
            f"[users] scraping {len(usernames)} Devpost profiles "
            f"({workers} workers)...",
            flush=True,
        )
        workers = max(1, min(workers, len(usernames)))

        def _job(username: str) -> tuple[str, list[str], list[str]]:
            local = DevpostScraper(HttpClient(), self.config)
            repos, users = local.enrich_user_profile(username)
            return username, repos, users

        results: dict[str, tuple[list[str], list[str]]] = {}
        if workers == 1:
            for u in usernames:
                _, repos, users = _job(u)
                results[u.lower()] = (repos, users)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {pool.submit(_job, u): u for u in usernames}
                for fut in as_completed(futs):
                    username = futs[fut]
                    try:
                        _, repos, users = fut.result()
                        results[username.lower()] = (repos, users)
                    except Exception as exc:
                        print(f"  user scrape failed [{username}]: {exc}", flush=True)
                        results[username.lower()] = ([], [])

        # Only merge GitHub *usernames* from profiles — never every portfolio repo
        # (those get attached later only if discovery matches the project name).
        gained_users = 0
        for p in projects:
            before_u = len(p.github_users)
            for profile in p.team_profiles:
                _repos, users = results.get(profile.lower(), ([], []))
                _merge_unique(p.github_users, users)
            gained_users += max(0, len(p.github_users) - before_u)

        print(
            f"[users] done — +{gained_users} GitHub user(s) "
            f"(repos left to name/window discovery)",
            flush=True,
        )
