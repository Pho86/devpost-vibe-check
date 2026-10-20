"""CLI entry — argument parsing only; orchestration lives in pipeline.py."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .config import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TZ,
    DEFAULT_WORKERS,
    EARLY_WINDOW_HOURS,
    LOW_COMMIT_THRESHOLD,
    PRESETS,
    RunConfig,
)
from .pipeline import parse_dt, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="devpost-vibe-check",
        description=(
            "Rank Devpost hackathon submissions by suspicious git history. "
            "Primary input: organizer projects export CSV. "
            "Attendee CSVs (portal / Luma / registrants) are optional."
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
            "Example: --start 2026-10-03T12:00:00 --end 2026-10-04T12:00:00 "
            "--tz America/Los_Angeles"
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
        default=os.environ.get("CHECKIN_COLUMN") or None,
        help=(
            "Check-in column name in portal/check-in CSV. "
            "Omit to auto-detect (e.g. 'Checked In', 'check-in')."
        ),
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
    p.add_argument(
        "--publish",
        action="store_true",
        default=os.environ.get("VIBE_PUBLISH", "").strip().lower() in {"1", "true", "yes", "on"},
        help=(
            "After the run, upload output/review to Netlify under a unique share "
            "code (needs NETLIFY_AUTH_TOKEN). Or set VIBE_PUBLISH=1."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def run(argv: list[str] | None = None) -> int:
    load_dotenv()

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
        start = parse_dt(args.start, args.tz)
        end = parse_dt(args.end, args.tz)
    except Exception as exc:
        print(f"ERROR: bad --start/--end/--tz ({exc})", flush=True)
        return 2
    if end <= start:
        print("ERROR: --end must be after --start", flush=True)
        return 2

    # Precedence: explicit --min-score > --preset default.
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
        publish=bool(args.publish),
    )

    return run_pipeline(config, use_gallery=use_gallery)


def main() -> None:
    raise SystemExit(run())
