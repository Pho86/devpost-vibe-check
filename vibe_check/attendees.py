from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .models import Project

# Values that mean "this person is allowed at the hackathon".
ACCEPTED_VALUES = {
    "accepted",
    "approved",
    "going",
    "confirmed",
    "admitted",
    "registered",
    "yes",
    "true",
    "y",
    "1",
    "checked in",
    "checked-in",
    "attended",
    "active",
}

REJECTED_VALUES = {
    "rejected",
    "declined",
    "denied",
    "waitlist",
    "waitlisted",
    "pending",
    "pending_approval",
    "invited",
    "not going",
    "no",
    "false",
    "0",
    "n/a",
    "na",
    "",
}

STATUS_HEADER_HINTS = (
    "current status",
    "approval status",
    "approval_status",
    "guest status",
    "registration status",
    "application status",
    "status",
)

EMAIL_HEADER_HINTS = ("email", "e-mail")


@dataclass
class AttendeeInfo:
    """Merged view of one email across Luma / portal / registrants / check-in."""

    email: str
    accepted: bool = False
    checked_in: bool = False
    # True if a source file had a check-in column for this person (so False checked_in is meaningful).
    checkin_tracked: bool = False
    statuses: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


def _norm_email(v: object) -> str:
    s = str(v or "").strip().lower()
    return "" if s in {"", "nan", "none"} else s


def _norm_status(v: object) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip().lower())


def _is_truthy_checkin(v: object) -> bool:
    s = _norm_status(v)
    if not s or s in {"nan", "none", "false", "0", "no", "n/a", "na"}:
        return False
    return True


def _status_means_accepted(status: str) -> bool | None:
    """True / False / None (unknown — don't infer)."""
    s = _norm_status(status)
    if not s:
        return None
    if s in ACCEPTED_VALUES:
        return True
    if s in REJECTED_VALUES:
        return False
    # Partial contains (e.g. "Accepted - Travel")
    for a in ACCEPTED_VALUES:
        if a and a in s:
            return True
    for r in ("reject", "decline", "waitlist", "pending"):
        if r in s:
            return False
    return None


def _detect_preset(fieldnames: list[str]) -> str:
    lows = [((f or "").lower()) for f in fieldnames]
    joined = " | ".join(lows)
    if "approval status" in joined or "api_id" in joined or "ticket type" in joined:
        return "luma"
    if "current status" in joined and any("check" in x and "in" in x for x in lows):
        return "portal"
    if "current status" in joined:
        return "portal"
    if "submitted project?" in joined or "registered at" in joined:
        return "registrants"
    return "generic"


def _pick_columns(
    fieldnames: list[str],
    *,
    status_column: str | None = None,
    checkin_column: str | None = None,
) -> tuple[list[str], str | None, str | None]:
    email_cols = [
        c for c in fieldnames if c and any(h in c.lower() for h in EMAIL_HEADER_HINTS)
    ]
    status_col = None
    if status_column:
        for c in fieldnames:
            if c == status_column:
                status_col = c
                break
    if status_col is None:
        # Prefer longer / more specific status headers first.
        ranked: list[tuple[int, str]] = []
        for c in fieldnames:
            if not c:
                continue
            cl = c.lower().strip()
            for i, hint in enumerate(STATUS_HEADER_HINTS):
                if cl == hint or hint in cl:
                    # Lower i = better; prefer exact-ish.
                    ranked.append((i, c))
                    break
        if ranked:
            ranked.sort(key=lambda t: t[0])
            status_col = ranked[0][1]

    check_col = None
    if checkin_column:
        for c in fieldnames:
            if c == checkin_column:
                check_col = c
                break
    if check_col is None:
        for c in fieldnames:
            if not c:
                continue
            cl = c.lower()
            if "check" in cl and "in" in cl:
                check_col = c
                break
        if check_col is None:
            for c in fieldnames:
                if c and c.lower().strip() in {"checked in", "checked_in", "check-in"}:
                    check_col = c
                    break
    return email_cols, status_col, check_col


def load_attendee_csv(
    path: Path,
    *,
    source_label: str | None = None,
    preset: str = "auto",
    status_column: str | None = None,
    checkin_column: str | None = None,
    # When True and no status column: presence in file ⇒ accepted (Devpost registrants).
    presence_means_accepted: bool | None = None,
) -> dict[str, AttendeeInfo]:
    """
    Load one attendees CSV (Luma guest export, hacker portal, Devpost registrants, …).

    Returns email → AttendeeInfo for that file only.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Attendees CSV not found: {path}")

    label = source_label or path.name
    out: dict[str, AttendeeInfo] = {}

    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    if df.empty or df.columns.empty:
        return out

    fields = [str(c) for c in df.columns.tolist()]
    detected = _detect_preset(fields) if preset == "auto" else preset
    email_cols, status_col, check_col = _pick_columns(
        fields, status_column=status_column, checkin_column=checkin_column
    )

    if presence_means_accepted is None:
        # Registrants lists / email-only dumps: listed ⇒ accepted.
        presence_means_accepted = detected in {"registrants", "generic"} and (
            status_col is None
        )

    for _, row in df.iterrows():
        emails: list[str] = []
        for col in email_cols:
            e = _norm_email(row.get(col))
            if e and e not in emails:
                emails.append(e)
        if not emails:
            continue

        raw_status = row.get(status_col) if status_col else ""
        accepted_vote = _status_means_accepted(raw_status) if status_col else None
        if accepted_vote is None and presence_means_accepted:
            accepted_vote = True

        checked = _is_truthy_checkin(row.get(check_col)) if check_col else False

        for e in emails:
            info = out.get(e) or AttendeeInfo(email=e)
            if accepted_vote is True:
                info.accepted = True
            if check_col:
                info.checkin_tracked = True
                if checked:
                    info.checked_in = True
            if raw_status and str(raw_status).strip():
                st = str(raw_status).strip()
                if st not in info.statuses:
                    info.statuses.append(st)
            if label not in info.sources:
                info.sources.append(label)
            if accepted_vote is False and not info.accepted:
                if "not_accepted" not in info.statuses:
                    info.statuses.append("not_accepted")
            out[e] = info

    print(
        f"[attendees] {label} ({detected}): {len(out)} emails "
        f"(status={status_col or '-'}, checkin={check_col or '-'}, "
        f"presence_accepted={presence_means_accepted})",
        flush=True,
    )
    return out


def merge_attendee_indexes(
    indexes: list[dict[str, AttendeeInfo]],
) -> dict[str, AttendeeInfo]:
    merged: dict[str, AttendeeInfo] = {}
    for idx in indexes:
        for e, info in idx.items():
            cur = merged.get(e) or AttendeeInfo(email=e)
            cur.accepted = cur.accepted or info.accepted
            cur.checked_in = cur.checked_in or info.checked_in
            cur.checkin_tracked = cur.checkin_tracked or info.checkin_tracked
            for s in info.statuses:
                if s not in cur.statuses:
                    cur.statuses.append(s)
            for src in info.sources:
                if src not in cur.sources:
                    cur.sources.append(src)
            merged[e] = cur
    return merged


def attach_attendees(projects: list[Project], index: dict[str, AttendeeInfo]) -> None:
    """
    Join team emails to the accepted/check-in index.

    - accepted_members / not_accepted / accepted_unmatched
    - checked_in_members / unchecked / unmatched (check-in view)
    """
    for p in projects:
        accepted = 0
        not_accepted: list[str] = []
        accepted_unmatched: list[str] = []
        checked = 0
        unchecked: list[str] = []
        checkin_unmatched: list[str] = []

        for e in p.team_emails:
            info = index.get(e)
            if info is None:
                accepted_unmatched.append(e)
                checkin_unmatched.append(e)
                continue
            if info.accepted:
                accepted += 1
            else:
                not_accepted.append(e)
            if info.checkin_tracked:
                if info.checked_in:
                    checked += 1
                else:
                    unchecked.append(e)

        p.accepted_members = accepted
        p.not_accepted_emails = not_accepted
        p.accepted_unmatched_emails = accepted_unmatched
        p.checked_in_members = checked
        p.unchecked_emails = unchecked
        p.unmatched_emails = checkin_unmatched
        if not p.team_size:
            p.team_size = len(p.team_emails)


# --- Back-compat wrappers used by older CLI paths ---


def load_checkin_index(
    path: Path,
    *,
    checkin_column: str = "StormHacks 2026 Check In",
) -> dict[str, bool]:
    """Legacy: email → checked_in bool (portal-style)."""
    idx = load_attendee_csv(
        path,
        source_label=path.name,
        preset="auto",
        checkin_column=checkin_column,
        presence_means_accepted=True,  # portal rows imply registered applicants
    )
    return {e: info.checked_in for e, info in idx.items()}


def attach_checkin(projects: list[Project], index: dict[str, bool]) -> None:
    """Legacy check-in-only attach."""
    rich = {
        e: AttendeeInfo(email=e, accepted=True, checked_in=checked, sources=["checkin"])
        for e, checked in index.items()
    }
    attach_attendees(projects, rich)
