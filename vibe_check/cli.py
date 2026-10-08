from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from . import __version__
from .attendees import (
    attach_attendees,
    load_attendee_csv,
    merge_attendee_indexes,
)
from .config import (
    CHECKPOINT_EVERY,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TZ,
    DEFAULT_WORKERS,
    EARLY_WINDOW_HOURS,
    LOW_COMMIT_THRESHOLD,
    PRESETS,
    RunConfig,
)
from .github_client import GitHubClient
from .http_util import HttpClient
from .models import RepoAnalysis, VibeResult, normalize_repo_slug
from .projects_csv import load_projects_csv
from .report import (
    clear_output_dir,
    print_flagged_highlights,
    print_summary,
    print_table,
    write_csv,
    write_json,
    write_markdown,
)
from .scoring import bucket_results, score_project
from .scraper import DevpostScraper, guess_hackathon_url


def _parse_dt(value: str, tz_name: str) -> datetime:
    """
    Parse ISO-8601. Naive values (no offset) are interpreted in --tz
    (default America/Los_Angeles), so 12:00 means noon Pacific.
    """
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(UTC)


def _fmt_local(dt_utc: datetime, tz_name: str) -> str:
    local = dt_utc.astimezone(ZoneInfo(tz_name))
    return local.strftime("%Y-%m-%d %I:%M %p %Z")


def _result_from_dict(r: dict) -> VibeResult:
    flags = r.get("flags") or []
    reasons = r.get("reasons") or []
    if isinstance(flags, str):
        flags = [x for x in flags.split("|") if x]
    if isinstance(reasons, str):
        reasons = [x for x in reasons.split(" | ") if x]
    repos = r.get("repos") or []
    if isinstance(repos, str):
        repos = [x for x in repos.split("|") if x]
    repos = [normalize_repo_slug(x) for x in repos if x]
    primary = normalize_repo_slug(r.get("primary_repo") or r.get("repo") or "")
    return VibeResult(
        suspicion_score=int(r.get("suspicion_score") or 0),
        flags=flags,
        reasons=reasons,
        bucket=r.get("bucket") or "",
        project_title=r.get("project_title") or "",
        project_url=r.get("project_url") or "",
        project_slug=r.get("project_slug") or "",
        repos=repos,
        primary_repo=primary,
        status=r.get("status") or "",
        likely_noncode=bool(r.get("likely_noncode")),
        total_commits=r.get("total_commits"),
        pre_start_commits=int(r.get("pre_start_commits") or 0),
        post_end_commits=int(r.get("post_end_commits") or 0),
        in_window_commits=int(r.get("in_window_commits") or 0),
        outside_window_commits=int(r.get("outside_window_commits") or 0),
        early_window_commits=int(r.get("early_window_commits") or 0),
        created_at=r.get("created_at") or "",
        created_before_start=bool(r.get("created_before_start")),
        first_meaningful_commit_at=r.get("first_meaningful_commit_at") or "",
        is_fork=bool(r.get("is_fork")),
        fork_parent=r.get("fork_parent") or "",
        repo_size_kb=int(r.get("repo_size_kb") or 0),
        bulk_dump_suspect=bool(r.get("bulk_dump_suspect")),
        email_overlap=int(r.get("email_overlap") or 0),
        checked_in_members=int(r.get("checked_in_members") or 0),
        team_size=int(r.get("team_size") or 0),
        author_emails=(
            [x for x in (r.get("author_emails") or "").split("|") if x]
            if isinstance(r.get("author_emails"), str)
            else list(r.get("author_emails") or [])
        ),
        team_emails=(
            [x for x in (r.get("team_emails") or "").split("|") if x]
            if isinstance(r.get("team_emails"), str)
            else list(r.get("team_emails") or [])
        ),
        earliest_commit_at=r.get("earliest_commit_at") or "",
        latest_commit_at=r.get("latest_commit_at") or "",
        sample_messages=r.get("sample_messages") or "",
        repo_details=r.get("repo_details") or "",
        team_profiles=(
            (r.get("team_profiles") or "").split("|")
            if isinstance(r.get("team_profiles"), str)
            else list(r.get("team_profiles") or [])
        ),
        relevance_hits=(
            (r.get("relevance_hits") or "").split("|")
            if isinstance(r.get("relevance_hits"), str)
            else list(r.get("relevance_hits") or [])
        ),
        relevance_sources=(
            (r.get("relevance_sources") or "").split("|")
            if isinstance(r.get("relevance_sources"), str)
            else list(r.get("relevance_sources") or [])
        ),
        name_match_score=float(r.get("name_match_score") or 0),
        gemini_verdict=r.get("gemini_verdict") or "",
        gemini_confidence=int(r.get("gemini_confidence") or 0),
        gemini_summary=r.get("gemini_summary") or "",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="devpost-vibe-check",
        description=(
            "Rank Devpost hackathon submissions by suspicious git history. "
            "Primary input: organizer projects export CSV."
        ),
    )
    p.add_argument(
        "--projects-csv",
        type=Path,
        default=os.environ.get("PROJECTS_CSV") or None,
        help="Devpost organizer projects export CSV (or PROJECTS_CSV).",
    )
    p.add_argument(
        "--from-gallery",
        action="store_true",
        help="Optional: scrape public /project-gallery (requires --hackathon).",
    )
    scrape = p.add_mutually_exclusive_group()
    scrape.add_argument(
        "--scrape-devpost",
        dest="scrape_devpost",
        action="store_true",
        default=True,
        help=(
            "Scrape each submission's Devpost page for GitHub links + team profiles "
            "(default on). Also gallery-resolves blank Submission Url rows."
        ),
    )
    scrape.add_argument(
        "--no-scrape-devpost",
        dest="scrape_devpost",
        action="store_false",
        help="Skip Devpost page + gallery-resolve scraping.",
    )
    # Back-compat aliases
    scrape.add_argument(
        "--scrape-missing",
        dest="scrape_devpost",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    scrape.add_argument(
        "--no-scrape-missing",
        dest="scrape_devpost",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    users = p.add_mutually_exclusive_group()
    users.add_argument(
        "--scrape-users",
        dest="scrape_users",
        action="store_true",
        default=True,
        help="Scrape Devpost team member profiles for GitHub users/repos (default on).",
    )
    users.add_argument(
        "--no-scrape-users",
        dest="scrape_users",
        action="store_false",
        help="Skip team profile scraping.",
    )
    p.add_argument(
        "--hackathon",
        default=os.environ.get("HACKATHON_URL"),
        help=(
            "Devpost hackathon base URL, e.g. https://your-event.devpost.com "
            "(auto-detected from Submission Url when possible; needed to resolve "
            "blank Submission Url rows via the gallery)."
        ),
    )
    p.add_argument(
        "--tz",
        default=os.environ.get("HACKATHON_TZ") or DEFAULT_TZ,
        help=(
            f"Timezone for naive --start/--end times (default: {DEFAULT_TZ}). "
            "Example: --start 2026-10-03T12:00:00 --end 2026-10-04T12:00:00 --tz America/Los_Angeles"
        ),
    )
    p.add_argument(
        "--start",
        default=os.environ.get("HACKATHON_START"),
        help="Hacking start ISO-8601 (naive = local in --tz). Or HACKATHON_START.",
    )
    p.add_argument(
        "--end",
        default=os.environ.get("HACKATHON_END"),
        help="Hacking end ISO-8601 (naive = local in --tz). Or HACKATHON_END.",
    )
    p.add_argument(
        "--preset",
        choices=sorted(PRESETS.keys()),
        default="review",
        help="Marked-bucket threshold preset: review=30, strict=15, loose=50 (default: review).",
    )
    p.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="Override preset: score >= this goes to marked_* outputs.",
    )
    p.add_argument(
        "--low-commits",
        type=int,
        default=LOW_COMMIT_THRESHOLD,
        help=f"Low-commit threshold (default: {LOW_COMMIT_THRESHOLD}).",
    )
    p.add_argument(
        "--early-hours",
        type=int,
        default=EARLY_WINDOW_HOURS,
        help=f"Too-early window hours after start (default: {EARLY_WINDOW_HOURS}).",
    )
    p.add_argument("--limit", type=int, default=None, help="Only first N projects.")
    p.add_argument("--max-pages", type=int, default=None, help="Gallery page cap.")
    p.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("GITHUB_WORKERS") or DEFAULT_WORKERS),
        help=f"Parallel GitHub workers (default: {DEFAULT_WORKERS}).",
    )
    p.add_argument(
        "--checkin-csv",
        type=Path,
        default=os.environ.get("CHECKIN_CSV") or None,
        help="Optional portal CSV (status + check-in). Same as --portal-csv.",
    )
    p.add_argument(
        "--checkin-column",
        default=os.environ.get("CHECKIN_COLUMN") or "StormHacks 2026 Check In",
        help="Check-in column name in portal/check-in CSV.",
    )
    p.add_argument(
        "--accepted-csv",
        type=Path,
        action="append",
        default=None,
        help=(
            "Accepted-attendee CSV (repeatable). Auto-detects Luma / portal / "
            "Devpost registrants / generic email lists."
        ),
    )
    p.add_argument(
        "--luma-csv",
        type=Path,
        default=os.environ.get("LUMA_CSV") or None,
        help="Luma guest export CSV (approval status → accepted).",
    )
    p.add_argument(
        "--portal-csv",
        type=Path,
        default=os.environ.get("PORTAL_CSV") or None,
        help="Hacker portal export (Current Status=Accepted, optional check-in).",
    )
    p.add_argument(
        "--registrants-csv",
        type=Path,
        default=os.environ.get("REGISTRANTS_CSV") or None,
        help="Devpost registrants export (listed email → accepted).",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for reports.",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Skip project slugs already in output/all_results.json.",
    )
    p.add_argument(
        "--gemini",
        action="store_true",
        help=(
            "Optional: second-pass review with Gemini (needs GEMINI_API_KEY and "
            "pip install google-genai). Scans marked + near-misses by default."
        ),
    )
    p.add_argument(
        "--gemini-all",
        action="store_true",
        help="With --gemini, scan every project (costlier).",
    )
    p.add_argument(
        "--gemini-model",
        default=os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL,
        help=f"Gemini model id (default: {DEFAULT_GEMINI_MODEL}).",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def run(argv: list[str] | None = None) -> int:
    load_dotenv()
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    args = build_parser().parse_args(argv)

    if not args.start or not args.end:
        print(
            "ERROR: pass --start and --end (ISO-8601), or set HACKATHON_START / HACKATHON_END",
            flush=True,
        )
        return 2

    use_gallery = bool(args.from_gallery)
    projects_csv = Path(args.projects_csv) if args.projects_csv else None

    if use_gallery and projects_csv:
        print("ERROR: use either --projects-csv or --from-gallery, not both", flush=True)
        return 2
    if not use_gallery and not projects_csv:
        print(
            "ERROR: organizer export required. Pass --projects-csv path/to/export.csv "
            "(or PROJECTS_CSV).\n"
            "  Optional fallback: --from-gallery --hackathon https://your-event.devpost.com",
            flush=True,
        )
        return 2
    if use_gallery and not args.hackathon:
        print("ERROR: --from-gallery requires --hackathon", flush=True)
        return 2

    try:
        start = _parse_dt(args.start, args.tz)
        end = _parse_dt(args.end, args.tz)
    except Exception as exc:
        print(f"ERROR: bad --start/--end/--tz ({exc})", flush=True)
        return 2
    if end <= start:
        print("ERROR: --end must be after --start", flush=True)
        return 2

    min_score = args.min_score if args.min_score is not None else PRESETS[args.preset]
    hackathon_url = (args.hackathon or "").rstrip("/") or "csv-import"

    config = RunConfig(
        hackathon_url=hackathon_url,
        start=start,
        end=end,
        low_commit_threshold=args.low_commits,
        early_window_hours=args.early_hours,
        limit=args.limit,
        max_pages=args.max_pages,
        output_dir=args.output_dir,
        resume=args.resume,
        min_score=min_score,
        preset=args.preset,
        projects_csv=projects_csv,
        scrape_devpost=bool(args.scrape_devpost),
        scrape_users=bool(args.scrape_users),
        workers=max(1, args.workers),
        checkin_csv=Path(args.checkin_csv) if args.checkin_csv else None,
        checkin_column=args.checkin_column,
        accepted_csvs=tuple(Path(p) for p in (args.accepted_csv or [])),
        luma_csv=Path(args.luma_csv) if args.luma_csv else None,
        portal_csv=Path(args.portal_csv) if args.portal_csv else None,
        registrants_csv=Path(args.registrants_csv) if args.registrants_csv else None,
        display_tz=args.tz,
        gemini=bool(args.gemini),
        gemini_model=str(args.gemini_model or DEFAULT_GEMINI_MODEL),
        gemini_all=bool(args.gemini_all),
    )

    # Fresh run → clear prior clutter (keep checkpoint if --resume).
    if not args.resume:
        clear_output_dir(config.output_dir)
        print(f"Cleared {config.output_dir}", flush=True)

    http = HttpClient()
    scraper = DevpostScraper(http, config)
    github = GitHubClient(http, config)

    source = (
        f"gallery ({config.gallery_url})"
        if use_gallery
        else f"organizer export ({config.projects_csv})"
    )
    print(
        f"Devpost vibe check\n"
        f"  source    : {source}\n"
        f"  hackathon : {config.hackathon_url}\n"
        f"  window    : {_fmt_local(config.start, args.tz)}  ->  "
        f"{_fmt_local(config.end, args.tz)}\n"
        f"  window UTC: {config.start.isoformat()}  ->  {config.end.isoformat()}\n"
        f"  early cut : {_fmt_local(config.early_cutoff, args.tz)} "
        f"(start + {config.early_window_hours}h)\n"
        f"  preset    : {config.preset} (marked >= {config.min_score})\n"
        f"  stages    : csv → "
        f"{'devpost' if config.scrape_devpost or use_gallery else 'skip-devpost'} → "
        f"{'users' if config.scrape_users else 'skip-users'} → "
        f"github → commit-relevance"
        f"{' → gemini' if config.gemini else ''}\n"
        f"  attendees : "
        f"{'on' if config.has_attendee_csvs else 'off'}\n"
        f"  email overlap : "
        f"{'on (attendee csv)' if config.email_overlap_enabled else 'off'}\n"
        f"  gemini    : "
        f"{'on (' + config.gemini_model + (', all' if config.gemini_all else ', marked+near') + ')' if config.gemini else 'off'}\n"
        f"  workers   : {config.workers}\n",
        flush=True,
    )

    # --- Stage 1: CSV / gallery list ---
    print("[1/5] Loading projects...", flush=True)
    if use_gallery:
        projects = scraper.fetch_gallery_projects()
        print(f"Found {len(projects)} gallery projects", flush=True)
    else:
        assert config.projects_csv is not None
        projects = load_projects_csv(config.projects_csv, limit=config.limit)
        with_gh = sum(1 for p in projects if p.github_repos)
        blank_url = sum(1 for p in projects if not (p.url or "").startswith("http"))
        print(
            f"Loaded {len(projects)} submissions "
            f"({with_gh} with GitHub in export, {blank_url} missing Submission Url)",
            flush=True,
        )

        # Auto-detect hackathon host from submission URLs when not provided.
        if config.hackathon_url == "csv-import":
            guessed = guess_hackathon_url(projects)
            if guessed:
                config = RunConfig(
                    **{**config.__dict__, "hackathon_url": guessed}
                )
                scraper = DevpostScraper(http, config)
                github = GitHubClient(http, config)
                print(f"Auto-detected hackathon: {guessed}", flush=True)

        if config.scrape_devpost:
            scraper.resolve_missing_urls(projects)

    if config.has_attendee_csvs:
        indexes = []
        if config.luma_csv:
            indexes.append(
                load_attendee_csv(
                    config.luma_csv, source_label="luma", preset="luma"
                )
            )
        if config.portal_csv:
            indexes.append(
                load_attendee_csv(
                    config.portal_csv,
                    source_label="portal",
                    preset="portal",
                    checkin_column=config.checkin_column,
                )
            )
        if config.checkin_csv and (
            not config.portal_csv
            or Path(config.checkin_csv).resolve()
            != Path(config.portal_csv).resolve()
        ):
            indexes.append(
                load_attendee_csv(
                    config.checkin_csv,
                    source_label="checkin",
                    preset="portal",
                    checkin_column=config.checkin_column,
                    presence_means_accepted=True,
                )
            )
        if config.registrants_csv:
            indexes.append(
                load_attendee_csv(
                    config.registrants_csv,
                    source_label="registrants",
                    preset="registrants",
                    presence_means_accepted=True,
                )
            )
        for i, path in enumerate(config.accepted_csvs):
            indexes.append(
                load_attendee_csv(
                    path,
                    source_label=f"accepted[{i+1}]",
                    preset="auto",
                    checkin_column=config.checkin_column,
                )
            )
        merged = merge_attendee_indexes(indexes)
        attach_attendees(projects, merged)
        n_acc = sum(1 for a in merged.values() if a.accepted)
        n_ci = sum(1 for a in merged.values() if a.checked_in)
        print(
            f"Loaded attendee index: {len(merged)} emails "
            f"({n_acc} accepted, {n_ci} checked in)",
            flush=True,
        )

    all_path = config.output_dir / "all_results.json"
    done_slugs: set[str] = set()
    results: list[VibeResult] = []
    if config.resume and all_path.exists():
        prior = json.loads(all_path.read_text(encoding="utf-8"))
        prior_meta = prior.get("meta") or {}
        prior_start = str(prior_meta.get("start") or "")
        prior_end = str(prior_meta.get("end") or "")
        prior_min = prior_meta.get("min_score")
        mismatch = (
            prior_start
            and prior_end
            and (
                prior_start != config.start.isoformat()
                or prior_end != config.end.isoformat()
                or (
                    prior_min is not None
                    and int(prior_min) != int(config.min_score)
                )
            )
        )
        if mismatch:
            print(
                "ERROR: --resume checkpoint window/min_score does not match this run.\n"
                f"  checkpoint: {prior_start} → {prior_end} (min_score={prior_min})\n"
                f"  this run : {config.start.isoformat()} → {config.end.isoformat()} "
                f"(min_score={config.min_score})\n"
                "  Re-run without --resume, or use the same --start/--end/--min-score.",
                flush=True,
            )
            return 2
        prior_rows = prior.get("results") or []
        results = [_result_from_dict(r) for r in prior_rows]
        done_slugs = {r.project_slug for r in results if r.project_slug}
        print(f"Resume: skipping {len(done_slugs)} already-scored slugs", flush=True)

    pending = [p for p in projects if p.slug not in done_slugs]

    # --- Stage 2: Devpost submission pages (all with a URL) ---
    if config.scrape_devpost or use_gallery:
        need_scrape = [
            p
            for p in pending
            if (p.url or "").startswith("http")
            or (p.slug and p.slug.lower() != "untitled")
        ]
        print(
            f"[2/5] Scraping {len(need_scrape)} Devpost project pages "
            f"({config.workers} workers)...",
            flush=True,
        )
        if need_scrape:
            scraper.enrich_many(need_scrape, workers=config.workers)
            gained = sum(1 for p in need_scrape if p.github_repos)
            with_team = sum(1 for p in need_scrape if p.team_profiles)
            print(
                f"[2/5] done — {gained}/{len(need_scrape)} have GitHub, "
                f"{with_team} have team profiles",
                flush=True,
            )
    else:
        print("[2/5] Skipping Devpost scrape", flush=True)

    # --- Stage 3: team member Devpost profiles ---
    if config.scrape_users and (config.scrape_devpost or use_gallery):
        print("[3/5] Scraping Devpost team profiles...", flush=True)
        scraper.scrape_team_users(pending, workers=config.workers)
    else:
        print("[3/5] Skipping team profile scrape", flush=True)

    # --- Stage 4: discover matching repos on team GitHub users ---
    print("[4/5] Discovering GitHub repos from team users...", flush=True)
    discovered = github.discover_many(pending, workers=config.workers)
    print(f"[4/5] done — +{discovered} repo(s) matched by name/window", flush=True)

    # --- Stage 5: analyze repos + project/hackathon relevance ---
    print("[5/5] Analyzing GitHub history + commit relevance...", flush=True)
    analyses_by_project = github.analyze_projects(pending, workers=config.workers)

    from rich.progress import (
        BarColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        transient=True,
    ) as progress:
        task_id = progress.add_task("Scoring projects", total=len(pending) or 1)
        for i, project in enumerate(pending, 1):
            if not project.github_repos:
                result = score_project(project, [], config)
            else:
                by_repo = analyses_by_project.get(project.slug) or {}
                analyses = [by_repo[r] for r in project.github_repos if r in by_repo]
                if not analyses:
                    analyses = [
                        RepoAnalysis(
                            repo=project.github_repos[0],
                            status="analyze missed",
                            unavailable=True,
                        )
                    ]
                result = score_project(project, analyses, config)

            results.append(result)
            progress.update(
                task_id,
                advance=1,
                description=f"Scoring [{i}/{len(pending)}] {project.title[:40]}",
            )
            if i % CHECKPOINT_EVERY == 0 or i == len(pending):
                _checkpoint(config, results)

    # --- Optional Stage 6: Gemini second-pass ---
    if config.gemini:
        from .gemini_scan import GeminiScanner

        scanner = GeminiScanner(model=config.gemini_model)
        ok, why = scanner.available()
        if not ok:
            print(f"ERROR: --gemini requested but {why}", flush=True)
            return 2
        scanner.scan_many(
            results,
            config,
            marked_only=not config.gemini_all,
        )
        _checkpoint(config, results)

    marked, likely_safe, combined = bucket_results(
        results, min_score=config.min_score
    )

    summary = {
        "projects_loaded": len(projects),
        "scored": sum(1 for r in results if r.primary_repo or (r.repos)),
        "no_github": sum(1 for r in results if "NO_GITHUB" in r.flags),
        "unavailable": sum(1 for r in results if "REPO_UNAVAILABLE" in r.flags),
        "marked": len(marked),
        "likely_safe": len(likely_safe),
    }

    out = config.output_dir
    meta = {
        "hackathon_url": config.hackathon_url,
        "source": "gallery" if use_gallery else "organizer_export",
        "projects_csv": str(config.projects_csv) if config.projects_csv else None,
        "checkin_csv": str(config.checkin_csv) if config.checkin_csv else None,
        "luma_csv": str(config.luma_csv) if config.luma_csv else None,
        "portal_csv": str(config.portal_csv) if config.portal_csv else None,
        "registrants_csv": str(config.registrants_csv) if config.registrants_csv else None,
        "accepted_csvs": [str(p) for p in config.accepted_csvs],
        "tz": args.tz,
        "window_local": (
            f"{_fmt_local(config.start, args.tz)} → {_fmt_local(config.end, args.tz)}"
        ),
        "start": config.start.isoformat(),
        "end": config.end.isoformat(),
        "early_cutoff": config.early_cutoff.isoformat(),
        "low_commit_threshold": config.low_commit_threshold,
        "preset": config.preset,
        "min_score": config.min_score,
        "scrape_devpost": config.scrape_devpost,
        "scrape_users": config.scrape_users,
        "gemini": config.gemini,
        "gemini_model": config.gemini_model if config.gemini else None,
        "gemini_all": config.gemini_all,
        "workers": config.workers,
        **summary,
    }

    # One clean set of outputs per run (folder already cleared at start).
    write_csv(out / "marked.csv", marked)
    write_csv(out / "likely_safe.csv", likely_safe)
    write_csv(out / "combined.csv", combined)
    write_json(out / "marked.json", marked, meta)
    write_json(out / "likely_safe.json", likely_safe, meta)
    write_json(out / "combined.json", combined, meta)
    write_json(all_path, results, meta)
    write_markdown(
        out / "report.md",
        marked=marked,
        likely_safe=likely_safe,
        summary=summary,
        meta=meta,
    )

    print_summary(summary)
    print_flagged_highlights(marked, limit=10)
    print_table(marked, title="Flagged for review (not auto-DQ)", limit=25)
    print(
        f"\nWrote outputs (review queue first) ->\n"
        f"  {out / 'marked.csv'}\n"
        f"  {out / 'likely_safe.csv'}\n"
        f"  {out / 'combined.csv'}\n"
        f"  {out / 'report.md'}",
        flush=True,
    )
    return 0


def _checkpoint(config: RunConfig, results: list[VibeResult]) -> None:
    write_json(
        config.output_dir / "all_results.json",
        results,
        {
            "hackathon_url": config.hackathon_url,
            "checkpoint": True,
            "start": config.start.isoformat(),
            "end": config.end.isoformat(),
            "min_score": config.min_score,
        },
    )


def main() -> None:
    raise SystemExit(run())
