from __future__ import annotations

import csv
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .models import VibeResult, github_repo_url


def clear_output_dir(path: Path) -> None:
    """Wipe prior run artifacts; keep the directory + .gitkeep."""
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                pass

CSV_FIELDS = [
    "rank",
    "bucket",
    "suspicion_score",
    "flags",
    "reasons",
    "project_title",
    "project_url",
    "primary_repo",
    "repos",
    "status",
    "likely_noncode",
    "total_commits",
    "in_window_commits",
    "pre_start_commits",
    "post_end_commits",
    "outside_window_commits",
    "created_at",
    "created_before_start",
    "first_meaningful_commit_at",
    "is_fork",
    "fork_parent",
    "repo_size_kb",
    "bulk_dump_suspect",
    "email_overlap",
    "checked_in_members",
    "accepted_members",
    "team_size",
    "team_emails",
    "not_accepted_emails",
    "accepted_unmatched_emails",
    "earliest_commit_at",
    "latest_commit_at",
    "sample_messages",
    "repo_details",
    "team_profiles",
    "relevance_hits",
    "relevance_sources",
    "name_match_score",
    "gemini_verdict",
    "gemini_confidence",
    "gemini_summary",
]


def _ranked(rows: list[VibeResult]) -> list[VibeResult]:
    ordered = sorted(
        rows,
        key=lambda r: (
            -r.suspicion_score,
            r.total_commits if r.total_commits is not None else 9999,
            r.project_title.lower(),
        ),
    )
    for i, r in enumerate(ordered, 1):
        r.rank = i
    return ordered


def write_csv(path: Path, rows: list[VibeResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ranked = _ranked(rows)
    try:
        f = path.open("w", newline="", encoding="utf-8")
    except PermissionError:
        # Excel / IDE often locks the previous run's CSV.
        alt = path.with_name(path.stem + "_new" + path.suffix)
        print(f"WARNING: {path.name} locked — writing {alt.name} instead", flush=True)
        f = alt.open("w", newline="", encoding="utf-8")
        path = alt
    with f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in ranked:
            w.writerow(row.to_dict())


def write_json(path: Path, rows: list[VibeResult], meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ranked = _ranked(rows)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "meta": meta,
        "results": [r.to_dict() for r in ranked],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_markdown(
    path: Path,
    *,
    marked: list[VibeResult],
    likely_safe: list[VibeResult],
    summary: dict,
    meta: dict,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Devpost vibe check report",
        "",
        f"- Generated: `{datetime.now(UTC).isoformat()}`",
        f"- Window: `{meta.get('window_local') or (str(meta.get('start')) + ' → ' + str(meta.get('end')))}`",
        f"- Window (UTC): `{meta.get('start')}` → `{meta.get('end')}`",
        f"- Preset / min score: `{meta.get('preset')}` / `{meta.get('min_score')}`",
        f"- Source: `{meta.get('source')}`",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Projects loaded | {summary.get('projects_loaded', 0)} |",
        f"| Scored (had GitHub) | {summary.get('scored', 0)} |",
        f"| No GitHub | {summary.get('no_github', 0)} |",
        f"| Repo unavailable | {summary.get('unavailable', 0)} |",
        f"| Marked (review) | {summary.get('marked', 0)} |",
        f"| Likely safe | {summary.get('likely_safe', 0)} |",
        "",
        "## Marked projects (most suspicious first)",
        "",
    ]
    if not marked:
        lines.append("_None marked at this threshold._")
        lines.append("")
    else:
        lines += [
            "| # | Score | Project | Primary repo | Flags |",
            "|---:|---:|---|---|---|",
        ]
        for r in _ranked(marked)[:80]:
            repo_link = (
                f"[{r.primary_repo}]({github_repo_url(r.primary_repo)})"
                if r.primary_repo
                else "-"
            )
            lines.append(
                f"| {r.rank} | {r.suspicion_score} | "
                f"[{r.project_title}]({r.project_url}) | {repo_link} | "
                f"{', '.join(r.flags)} |"
            )
        lines.append("")

    lines += [
        "## Likely safe (lowest concern first still sorted by score desc)",
        "",
        f"Showing top 30 of {len(likely_safe)} (full list in CSV).",
        "",
        "| # | Score | Project | Primary repo | Flags |",
        "|---:|---:|---|---|---|",
    ]
    for r in _ranked(likely_safe)[:30]:
        repo_link = (
            f"[{r.primary_repo}]({github_repo_url(r.primary_repo)})"
            if r.primary_repo
            else "-"
        )
        lines.append(
            f"| {r.rank} | {r.suspicion_score} | "
            f"[{r.project_title}]({r.project_url}) | {repo_link} | "
            f"{', '.join(r.flags) or '-'} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def print_summary(summary: dict) -> None:
    console = Console()
    table = Table(title="Run summary")
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    for key in (
        "projects_loaded",
        "scored",
        "no_github",
        "unavailable",
        "marked",
        "likely_safe",
    ):
        label = "flagged (review)" if key == "marked" else key.replace("_", " ")
        table.add_row(label, str(summary.get(key, 0)))
    console.print(table)


def print_flagged_highlights(rows: list[VibeResult], *, limit: int = 10) -> None:
    """Top flagged projects with why — review queue, not a DQ list."""
    console = Console()
    ranked = _ranked(rows)
    if not ranked:
        console.print("[yellow]No projects flagged for review at this threshold.[/yellow]")
        return

    console.print(
        f"\n[bold]Top {min(limit, len(ranked))} flagged — verify manually "
        "(not auto-DQ)[/bold]"
    )
    for row in ranked[:limit]:
        repo = row.primary_repo or "-"
        console.print(
            f"  [red bold]{row.suspicion_score}[/red bold]  "
            f"[cyan]{row.project_title}[/cyan]  ({repo})"
        )
        console.print(f"     flags: {', '.join(row.flags) or '-'}")
        for reason in (row.reasons or [])[:2]:
            console.print(f"     • {reason}")
    if len(ranked) > limit:
        console.print(
            f"  … and {len(ranked) - limit} more in marked.csv / report.md"
        )


def print_table(rows: list[VibeResult], *, title: str, limit: int = 25) -> None:
    console = Console()
    ranked = _ranked(rows)
    if not ranked:
        console.print(f"[yellow]{title}: none[/yellow]")
        return

    table = Table(title=title)
    table.add_column("#", justify="right", style="bold")
    table.add_column("Score", justify="right", style="red")
    table.add_column("Flags")
    table.add_column("Why")
    table.add_column("Project")
    table.add_column("Repo")
    table.add_column("Commits", justify="right")
    table.add_column("Pre", justify="right")

    for row in ranked[:limit]:
        why = (row.reasons[0] if row.reasons else "-")[:56]
        table.add_row(
            str(row.rank),
            str(row.suspicion_score),
            ", ".join(row.flags)[:36],
            why,
            row.project_title[:32],
            (row.primary_repo or "-")[:24],
            "-" if row.total_commits is None else str(row.total_commits),
            str(row.pre_start_commits),
        )
    console.print(table)
    if len(ranked) > limit:
        console.print(f"... and {len(ranked) - limit} more (see marked.csv)")
