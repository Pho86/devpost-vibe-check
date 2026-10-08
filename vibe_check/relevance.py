from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import Project

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SPLIT_CAMEL = re.compile(r"([a-z])([A-Z])")

# Too generic for commit/repo matching or Devpost slug noise.
STOP_TOKENS = {
    "the",
    "and",
    "for",
    "app",
    "sim",
    "game",
    "hack",
    "project",
    "projects",
    "pronounced",
    "with",
    "from",
    "your",
    "our",
    "team",
    "demo",
    "test",
    "final",
    "new",
    "old",
    "www",
    "com",
    "http",
    "https",
    "follows",
    "notifications",
    "follow",
    "requests",
    # Generic English / framework words that false-trigger relevance
    "open",
    "rails",  # Ruby on Rails noise; compound titles still match via compact form
    "core",
    "base",
    "main",
    "data",
    "code",
    "soft",
    "ware",
    "system",
    "systems",
    "tool",
    "tools",
    "web",
    "api",
    "apps",
    "site",
    "page",
    "real",
    "time",
    "smart",
    "auto",
    "free",
    "best",
    "next",
    "fast",
}

# Devpost / site paths that look like profile URLs but aren't people.
SKIP_DEVPOST_USERS = {
    "hackathons",
    "software",
    "settings",
    "portfolio",
    "challenges",
    "blog",
    "login",
    "signup",
    "join",
    "about",
    "jobs",
    "enterprise",
    "organizations",
    "search",
    "terms",
    "privacy",
    "security",
    "api",
    "help",
    "support",
    "teams",
    "features",
    "pricing",
    "cleantech",
    "users",
    "register",
    "site",
    "pages",
    "forum",
    "forums",
    "newsletter",
    "follows",
    "notifications",
    "follow_requests",
    "messages",
    "home",
}


def _compact(s: str) -> str:
    return _NON_ALNUM.sub("", (s or "").lower())


def _tokens(text: str) -> list[str]:
    """Alphanumeric tokens + compacted whole string (min length 4)."""
    raw = (text or "").strip()
    if not raw:
        return []
    spaced = _SPLIT_CAMEL.sub(r"\1 \2", raw)
    parts = _NON_ALNUM.split(spaced.lower())
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if len(p) < 4 or p in STOP_TOKENS or p in seen:
            continue
        if p.isdigit():
            continue
        seen.add(p)
        out.append(p)
    whole = _compact(raw)
    # Strip leading submission ids: 1213968stormpucks → try trailing alpha chunk
    whole = re.sub(r"^\d{5,}", "", whole)
    if len(whole) >= 4 and whole not in seen and whole not in STOP_TOKENS:
        out.append(whole)
    return out


def hackathon_tokens(hackathon_url: str) -> list[str]:
    """
    From https://stormhacks2026.devpost.com → stormhacks2026, stormhacks, …
    """
    host = urlparse(hackathon_url or "").hostname or ""
    sub = host.split(".")[0] if host else ""
    toks = _tokens(sub)
    # Also strip trailing year digits: stormhacks2026 → stormhacks
    m = re.match(r"^([a-z]+?)(\d{2,4})$", _compact(sub))
    if m and len(m.group(1)) >= 4:
        base = m.group(1)
        if base not in toks:
            toks.append(base)
    return toks


def project_tokens(project: Project) -> list[str]:
    """Title + slug only — never tagline (too many generic words like 'robot')."""
    toks: list[str] = []
    seen: set[str] = set()
    for source in (project.title, project.slug):
        for t in _tokens(source):
            if t.isdigit():
                continue
            if t not in seen:
                seen.add(t)
                toks.append(t)
    return toks


def relevance_needles(project: Project, hackathon_url: str) -> list[str]:
    """Terms to hunt for in commits / README / repo name (longest first)."""
    needles: list[str] = []
    seen: set[str] = set()
    for t in project_tokens(project) + hackathon_tokens(hackathon_url):
        if t not in seen and len(t) >= 3:
            seen.add(t)
            needles.append(t)
    needles.sort(key=len, reverse=True)
    return needles


def find_mentions(blob: str, needles: list[str]) -> list[str]:
    """
    Find needles in text. Short needles (<6) require a word-ish boundary so
    'rail' does not hit inside unrelated words; long/compact forms may substring.
    """
    text = (blob or "").lower()
    compact = _compact(text)
    hits: list[str] = []
    for n in needles:
        if len(n) < 4:
            continue
        if len(n) >= 6:
            if n in text or n in compact:
                hits.append(n)
            continue
        # Short token: word boundary in text, or whole compact equality/containment
        # only when needle is a large fraction of a compact token chunk.
        if re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", text):
            hits.append(n)
        elif n == compact or (len(compact) <= len(n) + 2 and n in compact):
            hits.append(n)
    return hits


def name_similarity(repo_full_name: str, project: Project) -> float:
    """0..1 rough match between repo name and project title/slug (not tagline)."""
    repo = (repo_full_name or "").split("/")[-1]
    rc = _compact(repo)
    if not rc:
        return 0.0
    best = 0.0
    candidates = [project.title, project.slug]
    # Compact title/slug without leading submission id digits.
    for source in (project.title, project.slug):
        c = re.sub(r"^\d{5,}", "", _compact(source))
        if c:
            candidates.append(c)
    for cand in candidates:
        cc = _compact(cand)
        cc = re.sub(r"^\d{5,}", "", cc)
        if not cc or len(cc) < 4:
            continue
        if rc == cc:
            best = max(best, 1.0)
        elif len(cc) >= 5 and (rc in cc or cc in rc):
            # Require the shorter side to be a real chunk of the longer name.
            shorter, longer = (rc, cc) if len(rc) <= len(cc) else (cc, rc)
            if len(shorter) >= 5 and shorter in longer:
                best = max(best, 0.85)
        elif len(cc) >= 5 and rc[:5] == cc[:5]:
            best = max(best, 0.6)
    return best
