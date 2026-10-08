from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .models import Project
from .scraper import extract_repos

SUBMISSION_SLUG_RE = re.compile(r"/submissions/([^/?#]+)", re.I)
SOFTWARE_SLUG_RE = re.compile(r"/software/([^/?#]+)", re.I)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
TEXT_HINTS = ("Try it out", "About", "Video", "Notes", "Built", "Link")


def _normalize_header(header: list[str]) -> list[str]:
    cols = list(header)
    if cols and cols[-1].strip() == "...":
        cols = cols[:-1]
    while len(cols) < 37:
        idx = (len(cols) - 28) // 3 + 1
        part = ["First Name", "Last Name", "Email"][(len(cols) - 28) % 3]
        cols.append(f"Team Member {idx} {part}")
    return cols


def _slug_from_url(url: str, title: str) -> str:
    m = SUBMISSION_SLUG_RE.search(url) or SOFTWARE_SLUG_RE.search(url)
    if m:
        return m.group(1)
    cleaned = re.sub(r"[^a-z0-9]+", "-", (title or "project").lower()).strip("-")
    return cleaned or "project"


def _norm_email(v: object) -> str:
    s = str(v or "").strip().lower()
    return "" if s in {"", "nan", "none"} else s


def _collect_emails(header: list[str], cells: list[str]) -> list[str]:
    emails: list[str] = []
    seen: set[str] = set()
    for i, h in enumerate(header):
        if "email" not in h.lower():
            continue
        e = _norm_email(cells[i] if i < len(cells) else "")
        if e and e not in seen:
            seen.add(e)
            emails.append(e)
    blob = " ".join(cells)
    for m in EMAIL_RE.findall(blob):
        e = _norm_email(m)
        if e and e not in seen:
            seen.add(e)
            emails.append(e)
    return emails


def load_projects_csv(path: Path, *, limit: int | None = None) -> list[Project]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Projects CSV not found: {path}")

    # header=None: Devpost pads/truncates team columns; we normalize by position.
    raw = pd.read_csv(
        path,
        header=None,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )
    if raw.empty:
        return []

    header = _normalize_header([str(c) for c in raw.iloc[0].tolist()])
    body = raw.iloc[1:]
    if body.empty:
        return []

    try:
        title_i = header.index("Project Title")
        url_i = header.index("Submission Url")
    except ValueError as exc:
        raise ValueError(
            "CSV missing required columns 'Project Title' and/or 'Submission Url'."
        ) from exc

    try_i = next((i for i, h in enumerate(header) if "Try it out" in h), None)
    about_i = next((i for i, h in enumerate(header) if h.startswith("About")), None)
    built_i = next((i for i, h in enumerate(header) if "Built With" in h), None)
    prizes_i = next((i for i, h in enumerate(header) if "Opt-In Prize" in h), None)
    text_cols = [
        i for i, h in enumerate(header) if i != try_i and any(hint in h for hint in TEXT_HINTS)
    ]
    n = len(header)

    projects: list[Project] = []
    for row in body.itertuples(index=False, name=None):
        cells = [str(c or "") for c in row]
        if len(cells) < n:
            cells = cells + [""] * (n - len(cells))
        else:
            cells = cells[:n]
        if all(not c.strip() for c in cells):
            continue

        title = cells[title_i].strip()
        sub_url = cells[url_i].strip().rstrip("/")
        if not sub_url and not title:
            continue

        try_links = cells[try_i] if try_i is not None else ""
        blob = try_links + " " + " ".join(cells[i] for i in text_cols)
        repos = extract_repos(blob) or extract_repos(" ".join(cells))

        tagline = cells[about_i].strip()[:200] if about_i is not None else ""
        built = cells[built_i].strip() if built_i is not None else ""
        prizes = cells[prizes_i].strip() if prizes_i is not None else ""
        emails = _collect_emails(header, cells)
        slug = _slug_from_url(sub_url, title)

        projects.append(
            Project(
                title=title or slug or "untitled",
                # Blank when Devpost omitted Submission Url — gallery resolve fills it.
                url=sub_url,
                slug=slug,
                tagline=tagline,
                github_repos=repos,
                team_emails=emails,
                built_with=built,
                opt_in_prizes=prizes,
                try_links=try_links,
                team_size=len(emails),
            )
        )
        if limit is not None and len(projects) >= limit:
            break

    return projects
