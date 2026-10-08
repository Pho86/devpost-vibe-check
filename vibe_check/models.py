from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

_GH_URL_PREFIX = "https://github.com/"
_GH_REPO_RE = re.compile(
    r"^(?:https?://(?:www\.)?github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$",
    re.I,
)


def github_repo_url(repo: str) -> str:
    """owner/repo → https://github.com/owner/repo (pass-through if already a URL)."""
    raw = (repo or "").strip()
    if not raw or raw == "-":
        return ""
    if raw.lower().startswith("https://github.com/") or raw.lower().startswith(
        "http://github.com/"
    ):
        return raw.rstrip("/")
    m = _GH_REPO_RE.match(raw)
    if m:
        return f"{_GH_URL_PREFIX}{m.group(1)}/{m.group(2)}"
    if "/" in raw:
        return f"{_GH_URL_PREFIX}{raw.strip('/')}"
    return raw


def normalize_repo_slug(repo: str) -> str:
    """https://github.com/owner/repo or owner/repo → owner/repo."""
    raw = (repo or "").strip()
    if not raw:
        return ""
    m = _GH_REPO_RE.match(raw)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return raw


def mask_email(email: str) -> str:
    """alice@school.edu → a***@school.edu (keeps domain for organizer triage)."""
    e = (email or "").strip()
    if "@" not in e:
        return e
    local, _, domain = e.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


@dataclass
class Project:
    title: str
    url: str
    slug: str
    tagline: str = ""
    github_repos: list[str] = field(default_factory=list)
    github_users: list[str] = field(default_factory=list)
    team_profiles: list[str] = field(default_factory=list)  # Devpost usernames
    team_emails: list[str] = field(default_factory=list)
    built_with: str = ""
    opt_in_prizes: str = ""
    try_links: str = ""
    # Populated when joining portal / check-in CSV
    checked_in_members: int = 0
    team_size: int = 0
    unchecked_emails: list[str] = field(default_factory=list)
    unmatched_emails: list[str] = field(default_factory=list)


@dataclass
class RepoAnalysis:
    repo: str
    status: str = "ok"
    unavailable: bool = False
    total_commits: int | None = None
    pre_start_commits: int = 0
    post_end_commits: int = 0
    in_window_commits: int = 0
    outside_window_commits: int = 0
    early_window_commits: int = 0
    created_at: str = ""
    created_before_start: bool = False
    is_fork: bool = False
    fork_parent: str = ""
    default_branch: str = ""
    repo_size_kb: int = 0
    earliest_commit_at: str = ""
    latest_commit_at: str = ""
    first_meaningful_commit_at: str = ""
    sample_messages: str = ""
    author_emails: list[str] = field(default_factory=list)
    email_overlap: int = 0
    bulk_dump_suspect: bool = False
    all_commits_before_start: bool = False
    all_commits_after_end: bool = False
    all_commits_outside_window: bool = False
    all_commits_too_early: bool = False
    window_stats_incomplete: bool = False
    readme_excerpt: str = ""
    # Project / hackathon name found in repo name, recent commits, or README
    name_match_score: float = 0.0
    relevance_hits: list[str] = field(default_factory=list)
    relevance_sources: list[str] = field(default_factory=list)  # repo_name|commits|readme

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["author_emails"] = "|".join(self.author_emails)
        d["relevance_hits"] = "|".join(self.relevance_hits)
        d["relevance_sources"] = "|".join(self.relevance_sources)
        return d


@dataclass
class VibeResult:
    """One row = one Devpost project (repos collapsed)."""

    rank: int = 0
    bucket: str = ""  # marked | likely_safe | unscored
    suspicion_score: int = 0
    flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    project_title: str = ""
    project_url: str = ""
    project_slug: str = ""
    repos: list[str] = field(default_factory=list)
    primary_repo: str = ""
    status: str = ""
    likely_noncode: bool = False
    total_commits: int | None = None
    in_window_commits: int = 0
    pre_start_commits: int = 0
    post_end_commits: int = 0
    outside_window_commits: int = 0
    early_window_commits: int = 0
    created_at: str = ""
    created_before_start: bool = False
    first_meaningful_commit_at: str = ""
    is_fork: bool = False
    fork_parent: str = ""
    repo_size_kb: int = 0
    bulk_dump_suspect: bool = False
    author_emails: list[str] = field(default_factory=list)
    email_overlap: int = 0
    team_emails: list[str] = field(default_factory=list)
    checked_in_members: int = 0
    team_size: int = 0
    earliest_commit_at: str = ""
    latest_commit_at: str = ""
    sample_messages: str = ""
    repo_details: str = ""  # compact per-repo summary
    team_profiles: list[str] = field(default_factory=list)
    relevance_hits: list[str] = field(default_factory=list)
    relevance_sources: list[str] = field(default_factory=list)
    name_match_score: float = 0.0
    # Optional Gemini second-pass review
    gemini_verdict: str = ""  # suspicious | likely_ok | unclear | skipped | error
    gemini_confidence: int = 0
    gemini_summary: str = ""

    def to_dict(self, *, redact_pii: bool = True) -> dict[str, Any]:
        d = asdict(self)
        d["flags"] = "|".join(self.flags)
        d["reasons"] = " | ".join(self.reasons)
        # Export clickable GitHub URLs in CSV / JSON.
        d["primary_repo"] = github_repo_url(self.primary_repo)
        d["repos"] = "|".join(github_repo_url(r) for r in self.repos if r)
        authors = self.author_emails
        teams = self.team_emails
        if redact_pii:
            authors = [mask_email(e) for e in authors]
            teams = [mask_email(e) for e in teams]
        d["author_emails"] = "|".join(authors)
        d["team_emails"] = "|".join(teams)
        d["team_profiles"] = "|".join(self.team_profiles)
        d["relevance_hits"] = "|".join(self.relevance_hits)
        d["relevance_sources"] = "|".join(self.relevance_sources)
        if self.repo_details:
            # Rewrite leading owner/repo: in each detail segment.
            bits = []
            for part in self.repo_details.split(" ; "):
                if ":" in part:
                    head, rest = part.split(":", 1)
                    bits.append(f"{github_repo_url(head)}:{rest}")
                else:
                    bits.append(part)
            d["repo_details"] = " ; ".join(bits)
        return d
