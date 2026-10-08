from __future__ import annotations

import re

from .config import NONCODE_HINTS, RunConfig
from .models import Project, RepoAnalysis, VibeResult

INITIAL_MSG_RE = re.compile(
    r"^(initial commit|first commit|create repo|created repository)\b",
    re.I,
)

# Any of these = automatic mark (score floored to min_score). Pre-start = DQ.
PRE_START_DQ_FLAGS = frozenset(
    {
        "PRE_START_COMMITS",
        "ALL_COMMITS_BEFORE_START",
        "MEANINGFUL_WORK_BEFORE_START",
        "ALL_COMMITS_TOO_EARLY",
    }
)


def looks_noncode(project: Project) -> bool:
    # Don't scan github repo names — "hardware" in a software repo path is common.
    blob = " ".join(
        [
            project.tagline,
            project.built_with,
            project.opt_in_prizes,
            project.try_links,
        ]
    ).lower()
    if any(h in blob for h in NONCODE_HINTS):
        return True
    if "figma.com" in blob and not project.github_repos:
        return True
    return False


def _score_single_repo(
    project: Project,
    analysis: RepoAnalysis,
    config: RunConfig,
    *,
    noncode: bool,
) -> tuple[int, list[str], list[str]]:
    flags: list[str] = []
    reasons: list[str] = []
    score = 0
    total = analysis.total_commits
    threshold = config.low_commit_threshold

    if analysis.unavailable or analysis.status not in {"ok", ""}:
        if analysis.status == "commits fetch failed" or analysis.window_stats_incomplete:
            flags.append("COMMIT_FETCH_INCOMPLETE")
            reasons.append(analysis.status or "commit window stats incomplete")
            return 18, flags, reasons
        flags.append("REPO_UNAVAILABLE")
        reasons.append(analysis.status or "repo unavailable")
        return 12, flags, reasons  # mild — needs manual look, not max panic

    if analysis.window_stats_incomplete:
        flags.append("COMMIT_FETCH_INCOMPLETE")
        reasons.append("pre/post window counts may be truncated — treat cautiously")
        score += 8

    # --- low commits (softened for non-code / hardware) ---
    if total is not None and total < threshold:
        if noncode:
            low_score = 8 + (threshold - total) * 2  # much softer
            flags.append("LOW_COMMITS_NONCODE")
            reasons.append(
                f"only {total} commit(s), but looks hardware/Figma/no-code — soft flag"
            )
        else:
            low_score = 20 + (threshold - total) * 5
            flags.append("LOW_COMMITS")
            reasons.append(f"only {total} commit(s) (threshold < {threshold})")
        score += low_score

    # --- window signals ---
    if analysis.all_commits_outside_window and (total or 0) > 0:
        score += 50
        flags.append("ALL_COMMITS_OUTSIDE_WINDOW")
        reasons.append(
            f"every commit outside [{config.start.isoformat()} .. {config.end.isoformat()}]"
        )
    else:
        if analysis.all_commits_before_start and (total or 0) > 0:
            score += 45
            flags.append("ALL_COMMITS_BEFORE_START")
            reasons.append("every commit is before hacking started")
        elif analysis.pre_start_commits > 0:
            ratio = analysis.pre_start_commits / max(total or analysis.pre_start_commits, 1)
            # Heavy base — pre-start always marks (also floored below).
            score += 40 + int(ratio * 30)
            flags.append("PRE_START_COMMITS")
            reasons.append(
                f"{analysis.pre_start_commits} commit(s) before start "
                f"({analysis.earliest_commit_at or 'unknown'}) — automatic mark"
            )

        if analysis.all_commits_after_end and (total or 0) > 0:
            # Softened: polish after deadline is common
            score += 18
            flags.append("ALL_COMMITS_AFTER_END")
            reasons.append("every commit is after hacking ended")
        elif analysis.post_end_commits > 0:
            # Only nudge unless post-end dominates in-window work
            ratio = analysis.post_end_commits / max(total or analysis.post_end_commits, 1)
            bump = 3 + int(ratio * 8)
            if analysis.post_end_commits > max(analysis.in_window_commits, 1):
                bump += 6
                flags.append("POST_END_HEAVY")
                reasons.append(
                    f"{analysis.post_end_commits} post-end vs "
                    f"{analysis.in_window_commits} in-window commits"
                )
            else:
                flags.append("POST_END_COMMITS")
                reasons.append(
                    f"{analysis.post_end_commits} commit(s) after end "
                    f"(often late polish — soft flag)"
                )
            score += bump

    if (
        analysis.all_commits_too_early
        and not analysis.all_commits_before_start
        and not analysis.all_commits_outside_window
    ):
        score += 35
        flags.append("ALL_COMMITS_TOO_EARLY")
        reasons.append(
            f"all commits before start+{config.early_window_hours}h "
            f"({config.early_cutoff.isoformat()})"
        )

    # --- created_at vs first meaningful commit ---
    if analysis.created_before_start:
        score += 10
        flags.append("REPO_CREATED_BEFORE_START")
        reasons.append(f"repo created at {analysis.created_at}")
        if analysis.first_meaningful_commit_at:
            # Extra if meaningful work also pre-dates start
            try:
                from .github_client import parse_ts

                if parse_ts(analysis.first_meaningful_commit_at) < config.start:
                    score += 8
                    flags.append("MEANINGFUL_WORK_BEFORE_START")
                    reasons.append(
                        f"first meaningful commit at {analysis.first_meaningful_commit_at}"
                    )
            except Exception:
                pass

    if analysis.is_fork:
        score += 12
        flags.append("FORK")
        parent = f" (from {analysis.fork_parent})" if analysis.fork_parent else ""
        reasons.append(f"repository is a fork{parent}")

    # Initial-commit-only: skip if there is later in-window work
    if (
        total is not None
        and total <= 3
        and analysis.in_window_commits <= 1
        and analysis.sample_messages
        and INITIAL_MSG_RE.search(analysis.sample_messages.split("|")[0].strip())
        and analysis.in_window_commits + analysis.post_end_commits <= 1
    ):
        score += 8
        flags.append("INITIAL_COMMIT_ONLY")
        reasons.append("looks like a bare initial-commit dump")

    if analysis.bulk_dump_suspect:
        score += 22
        flags.append("BULK_DUMP")
        reasons.append(
            f"only {total} commit(s) but repo size ~{analysis.repo_size_kb}KB "
            "(possible prebuilt dump)"
        )

    # Author emails vs Devpost team — only when check-in CSV was provided.
    if (
        config.email_overlap_enabled
        and project.team_emails
        and analysis.author_emails
    ):
        from .github_client import _email_matches_team

        team = {e.lower() for e in project.team_emails}
        comparable = [
            e
            for e in analysis.author_emails
            if e and "users.noreply.github.com" not in e
        ]
        overlap = sum(1 for e in analysis.author_emails if _email_matches_team(e, team))
        analysis.email_overlap = overlap
        if overlap > 0:
            flags.append("EMAIL_OVERLAP")
            reasons.append(f"{overlap} Git author email(s) match Devpost team")
        elif comparable:
            # Only flag when there are real emails that failed to match.
            score += 10
            flags.append("NO_EMAIL_OVERLAP")
            reasons.append("no Git author email matches Devpost team emails")
        else:
            flags.append("AUTHOR_EMAILS_HIDDEN")
            reasons.append(
                "authors use GitHub noreply emails — overlap check skipped"
            )

    # Project / hackathon name in repo name, recent commits, or README.
    # Never use this to excuse pre-start / outside-window work.
    early_work = bool(
        analysis.created_before_start
        or analysis.pre_start_commits > 0
        or analysis.all_commits_before_start
        or analysis.all_commits_outside_window
    )
    if analysis.first_meaningful_commit_at:
        try:
            from .github_client import parse_ts

            if parse_ts(analysis.first_meaningful_commit_at) < config.start:
                early_work = True
        except Exception:
            pass

    if analysis.relevance_sources:
        in_commits = "commits" in analysis.relevance_sources
        flags.append("PROJECT_RELEVANCE")
        where = ", ".join(analysis.relevance_sources)
        hit = ", ".join(analysis.relevance_hits[:4]) or "name match"
        reasons.append(f"project/hackathon markers in {where} ({hit})")
        if in_commits:
            flags.append("PROJECT_IN_RECENT_COMMITS")
        if not early_work:
            # Soften only when the repo looks in-window / not prebuilt.
            score = max(0, score - (18 if in_commits else 10))
        else:
            reasons.append(
                "relevance softener skipped — repo has pre-start / outside-window work"
            )
    elif (total or 0) > 0 and (
        analysis.created_before_start
        or analysis.pre_start_commits > 0
        or analysis.name_match_score < 0.5
    ):
        # Old / mismatched repo with no project or hackathon string anywhere.
        score += 14
        flags.append("NO_PROJECT_RELEVANCE")
        reasons.append(
            "no project/hackathon name in repo name, recent commits, or README"
        )

    # Hard rule: any pre-start commit activity → always marked for review/DQ.
    if PRE_START_DQ_FLAGS.intersection(flags):
        floor = max(config.min_score, 30)
        if score < floor:
            score = floor
        if "PRE_START_DQ" not in flags:
            flags.append("PRE_START_DQ")
            reasons.append("pre-start work = automatic mark (instant fail)")

    return score, flags, reasons


def _is_submission_repo(analysis: RepoAnalysis, *, only_one: bool) -> bool:
    """Repos we trust as *this* submission (not a teammate portfolio attach)."""
    if only_one:
        return True
    if analysis.name_match_score >= 0.6:
        return True
    if analysis.relevance_sources:
        return True
    return False


def score_project(
    project: Project,
    analyses: list[RepoAnalysis],
    config: RunConfig,
) -> VibeResult:
    """Collapse linked repos into one project row (worst *candidate* repo)."""
    noncode = looks_noncode(project)

    if not analyses:
        return score_no_repo(project, config, noncode=noncode)

    only_one = len(analyses) == 1
    candidates = [a for a in analyses if _is_submission_repo(a, only_one=only_one)]
    scored_set = candidates or analyses  # fall back if nothing matched by name

    best_score = -1
    best_flags: list[str] = []
    best_reasons: list[str] = []
    best_analysis: RepoAnalysis | None = None
    all_flags: list[str] = []
    detail_bits: list[str] = []

    for analysis in analyses:
        s, flags, reasons = _score_single_repo(
            project, analysis, config, noncode=noncode
        )
        detail_bits.append(
            f"{analysis.repo}:score={s}:commits={analysis.total_commits}:"
            f"flags={','.join(flags) or '-'}"
        )
        if analysis not in scored_set:
            # Keep detail for transparency, but don't let portfolio junk DQ the project.
            continue
        for f in flags:
            if f not in all_flags:
                all_flags.append(f)
        if s > best_score:
            best_score = s
            best_flags = flags
            best_reasons = reasons
            best_analysis = analysis

    assert best_analysis is not None
    score = max(best_score, 0)
    flags = list(best_flags)
    reasons = list(best_reasons)

    # Prefer union of severe flags on the project row (candidates only).
    for severe in (
        "ALL_COMMITS_OUTSIDE_WINDOW",
        "ALL_COMMITS_BEFORE_START",
        "PRE_START_COMMITS",
        "PRE_START_DQ",
        "BULK_DUMP",
        "REPO_UNAVAILABLE",
        "COMMIT_FETCH_INCOMPLETE",
        "MEANINGFUL_WORK_BEFORE_START",
    ):
        if severe in all_flags and severe not in flags:
            flags.append(severe)

    # Hard rule: pre-start on a *candidate* repo → always marked.
    if PRE_START_DQ_FLAGS.intersection(all_flags) or "PRE_START_DQ" in all_flags:
        floor = max(config.min_score, 30)
        if score < floor:
            score = floor
        if "PRE_START_DQ" not in flags:
            flags.append("PRE_START_DQ")
            reasons.append("pre-start work on submission repo = automatic mark")

    if noncode and "LIKELY_NONCODE" not in flags:
        flags.append("LIKELY_NONCODE")
        reasons.append("signals look hardware / Figma / no-code")

    # Attendee/check-in flags only for already-marked heuristic scores
    # (CSVs are optional — join is a no-op when nothing was attached).
    if score >= config.min_score and (
        project.accepted_members
        or project.not_accepted_emails
        or project.accepted_unmatched_emails
        or project.checked_in_members
        or project.unchecked_emails
        or project.unmatched_emails
    ):
        score, flags, reasons = _apply_attendee_flags(project, score, flags, reasons)

    # Aggregate commit stats across repos (sum) for the row
    totals = [a.total_commits for a in analyses if a.total_commits is not None]
    return VibeResult(
        suspicion_score=score,
        flags=flags,
        reasons=reasons,
        project_title=project.title,
        project_url=project.url,
        project_slug=project.slug,
        repos=[a.repo for a in analyses],
        primary_repo=best_analysis.repo,
        status=best_analysis.status,
        likely_noncode=noncode,
        total_commits=sum(totals) if totals else None,
        in_window_commits=sum(a.in_window_commits for a in analyses),
        pre_start_commits=sum(a.pre_start_commits for a in analyses),
        post_end_commits=sum(a.post_end_commits for a in analyses),
        outside_window_commits=sum(a.outside_window_commits for a in analyses),
        early_window_commits=sum(a.early_window_commits for a in analyses),
        created_at=best_analysis.created_at,
        created_before_start=any(a.created_before_start for a in analyses),
        first_meaningful_commit_at=best_analysis.first_meaningful_commit_at,
        is_fork=any(a.is_fork for a in analyses),
        fork_parent=best_analysis.fork_parent,
        repo_size_kb=max((a.repo_size_kb for a in analyses), default=0),
        bulk_dump_suspect=any(a.bulk_dump_suspect for a in analyses),
        author_emails=sorted({e for a in analyses for e in a.author_emails}),
        email_overlap=sum(a.email_overlap for a in analyses),
        team_emails=list(project.team_emails),
        checked_in_members=project.checked_in_members,
        accepted_members=project.accepted_members,
        not_accepted_emails=list(project.not_accepted_emails),
        accepted_unmatched_emails=list(project.accepted_unmatched_emails),
        team_size=project.team_size,
        earliest_commit_at=min(
            (a.earliest_commit_at for a in analyses if a.earliest_commit_at),
            default="",
        ),
        latest_commit_at=max(
            (a.latest_commit_at for a in analyses if a.latest_commit_at),
            default="",
        ),
        sample_messages=best_analysis.sample_messages,
        repo_details=" ; ".join(detail_bits),
        team_profiles=list(project.team_profiles),
        relevance_hits=list(best_analysis.relevance_hits),
        relevance_sources=list(best_analysis.relevance_sources),
        name_match_score=best_analysis.name_match_score,
    )


def _apply_attendee_flags(
    project: Project,
    score: int,
    flags: list[str],
    reasons: list[str],
) -> tuple[int, list[str], list[str]]:
    """Accepted (Luma/portal/registrants) + check-in flags."""
    # --- Accepted into hackathon ---
    accepted_known = project.accepted_members + len(project.not_accepted_emails)
    if project.team_emails and accepted_known > 0:
        if project.accepted_members == 0:
            score += 30
            flags.append("TEAM_NOT_ACCEPTED")
            reasons.append(
                f"0/{accepted_known} matched team emails are accepted "
                f"({len(project.accepted_unmatched_emails)} not in attendee CSVs)"
            )
        elif project.not_accepted_emails:
            score += 12 + min(len(project.not_accepted_emails), 3) * 5
            flags.append("PARTIAL_ACCEPTED")
            reasons.append(
                f"{project.accepted_members}/{accepted_known} accepted; "
                f"not_accepted={len(project.not_accepted_emails)}"
            )
    elif project.team_emails and project.accepted_unmatched_emails and accepted_known == 0:
        # Attendee CSVs were joined but nobody matched — informational.
        if "ACCEPTED_UNMATCHED" not in flags:
            flags.append("ACCEPTED_UNMATCHED")
            reasons.append(
                f"{len(project.accepted_unmatched_emails)} team email(s) not found "
                "in Luma/portal/registrants CSVs"
            )

    # --- Check-in (day-of) ---
    matched_n = project.checked_in_members + len(project.unchecked_emails)
    if project.team_emails and matched_n > 0:
        if project.checked_in_members == 0:
            score += 25
            flags.append("TEAM_NOT_CHECKED_IN")
            reasons.append(
                f"0/{matched_n} matched team emails are checked in "
                f"({len(project.unmatched_emails)} unmatched)"
            )
        elif project.unchecked_emails:
            score += 10 + min(len(project.unchecked_emails), 3) * 5
            flags.append("PARTIAL_CHECKIN")
            reasons.append(
                f"{project.checked_in_members}/{matched_n} checked in; "
                f"unchecked={len(project.unchecked_emails)}"
            )
    elif project.team_emails and project.unmatched_emails and matched_n == 0:
        if "CHECKIN_UNMATCHED" not in flags:
            flags.append("CHECKIN_UNMATCHED")
            reasons.append(
                f"{len(project.unmatched_emails)} team email(s) not found in check-in data"
            )

    return score, flags, reasons


def score_no_repo(
    project: Project,
    config: RunConfig | None = None,
    *,
    noncode: bool | None = None,
) -> VibeResult:
    if noncode is None:
        noncode = looks_noncode(project)
    flags = ["NO_GITHUB"]
    reasons = ["no public GitHub repo found"]
    score = 0
    if noncode:
        flags.append("LIKELY_NONCODE")
        reasons.append("likely Figma/hardware/no-code — not inherently suspicious")
    else:
        score = 5
        reasons.append("software-looking submission with no GitHub link")

    min_score = config.min_score if config is not None else 30
    # Attendee/check-in only after the row is already marked (rare for no-repo).
    if score >= min_score and (
        project.accepted_members
        or project.not_accepted_emails
        or project.accepted_unmatched_emails
        or project.checked_in_members
        or project.unchecked_emails
        or project.unmatched_emails
    ):
        score, flags, reasons = _apply_attendee_flags(project, score, flags, reasons)

    return VibeResult(
        suspicion_score=score,
        flags=flags,
        reasons=reasons,
        project_title=project.title,
        project_url=project.url,
        project_slug=project.slug,
        status="no github link",
        likely_noncode=noncode,
        team_emails=list(project.team_emails),
        checked_in_members=project.checked_in_members,
        accepted_members=project.accepted_members,
        not_accepted_emails=list(project.not_accepted_emails),
        accepted_unmatched_emails=list(project.accepted_unmatched_emails),
        team_size=project.team_size,
    )


def bucket_results(
    results: list[VibeResult],
    *,
    min_score: int,
) -> tuple[list[VibeResult], list[VibeResult], list[VibeResult]]:
    """
    Returns (marked, likely_safe, combined).
    Sort order is always most suspicious first; ranks are assigned at write time.
    """
    marked: list[VibeResult] = []
    safe: list[VibeResult] = []
    for r in results:
        if r.suspicion_score >= min_score:
            r.bucket = "marked"
            marked.append(r)
        else:
            r.bucket = "likely_safe"
            safe.append(r)
    return marked, safe, list(results)
