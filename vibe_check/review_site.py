"""Generate a shareable static HTML review queue from run results."""

from __future__ import annotations

import csv
import html
import json
import re
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse

from .models import VibeResult, github_repo_url

_YEAR_SUFFIX = re.compile(r"^([a-z]+)(\d{2,4})$", re.I)

# Short organizer hints. Same idea as the README: look, do not auto-DQ.
FLAG_HINTS: dict[str, str] = {
    "PRE_START_REVIEW": "Priority look. Confirm timezone, repo, or reused homework.",
    "PRE_START_COMMITS": "Commits before start. Verify window and linked repo.",
    "ALL_COMMITS_BEFORE_START": "Entire history before start. Wrong repo or prebuilt?",
    "MEANINGFUL_WORK_BEFORE_START": "Non-trivial work before hacking started.",
    "LOW_COMMITS": "Very few commits. Possible zip dump or unfinished work.",
    "BULK_DUMP": "Tiny history + large tree. Check for a bulk upload.",
    "ALL_COMMITS_OUTSIDE_WINDOW": "No in-window commits. Wrong repo or outside event?",
    "NO_PROJECT_RELEVANCE": "Name/README do not match Devpost title. Confirm the link.",
    "REPO_UNAVAILABLE": "Private or 404. Ask the team for access; not guilt.",
    "TEAM_NOT_ACCEPTED": "Team emails missing from accepted list (already flagged).",
    "TEAM_NOT_CHECKED_IN": "Matched emails not checked in day-of (already flagged).",
    "GEMINI_SUSPICIOUS": "LLM second opinion only. Read the summary, then decide.",
    "NO_GITHUB": "No GitHub linked. May be hardware/Figma or a missing link.",
    "LIKELY_NONCODE": "Hardware / Figma / no-code signals. Low commits may be fine.",
}


def hackathon_display_name(hackathon_url: str) -> str:
    """https://stormhacks2026.devpost.com -> StormHacks2026"""
    host = urlparse(hackathon_url or "").hostname or ""
    sub = (host.split(".")[0] or "").strip()
    if not sub or sub.lower() in {"www", "devpost", "csv-import"}:
        return "Hackathon"
    m = _YEAR_SUFFIX.match(sub)
    if m:
        base, year = m.group(1), m.group(2)
        pretty = re.sub(r"(hack)", r"Hack", base, count=1, flags=re.I)
        pretty = pretty[0].upper() + pretty[1:]
        return f"{pretty}{year}"
    return sub[0].upper() + sub[1:] if sub else "Hackathon"


def _safe_url(url: str | None) -> str:
    """Only allow http(s) links so a bad Devpost field can't inject a javascript: URL."""
    u = (url or "").strip()
    return u if urlparse(u).scheme in {"http", "https"} else ""


def _csv_safe(value):
    """Neutralize spreadsheet formula injection in user-controlled text cells."""
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value


def _as_list(value) -> list[str]:
    """Coerce a list-or-delimited-string field into a clean list of strings.

    CSV imports hand back "A|B|C" strings; list("A|B") would split into characters.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split("|") if p.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _row_public(r: VibeResult) -> dict:
    """Shareable fields only; no team/author emails."""
    return {
        "rank": r.rank,
        "bucket": r.bucket,
        "suspicion_score": r.suspicion_score,
        "flags": _as_list(r.flags),
        "reasons": _as_list(r.reasons)[:6],
        "project_title": r.project_title,
        "project_url": _safe_url(r.project_url),
        "primary_repo": r.primary_repo,
        "primary_repo_url": _safe_url(github_repo_url(r.primary_repo)) if r.primary_repo else "",
        "total_commits": r.total_commits,
        "in_window_commits": r.in_window_commits,
        "pre_start_commits": r.pre_start_commits,
        "post_end_commits": r.post_end_commits,
        "sample_messages": (r.sample_messages or "")[:400],
        "name_match_score": r.name_match_score,
        "relevance_hits": _as_list(r.relevance_hits)[:8],
        "gemini_verdict": r.gemini_verdict,
        "gemini_summary": (r.gemini_summary or "")[:400],
        "likely_noncode": r.likely_noncode,
    }


def write_review_site(
    path: Path,
    *,
    marked: list[VibeResult],
    likely_safe: list[VibeResult],
    summary: dict,
    meta: dict,
) -> Path:
    """
    Write a single self-contained HTML file reviewers can open or host statically.
    Emails and other PII stay in CSV/JSON only, not on this page.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    from .report import _ranked

    marked_rows = [_row_public(r) for r in _ranked(marked)]
    safe_rows = [_row_public(r) for r in _ranked(likely_safe)]
    no_github_src = [r for r in (*marked, *likely_safe) if "NO_GITHUB" in _as_list(r.flags)]
    no_github_rows = [_row_public(r) for r in _ranked(no_github_src)]
    unavailable_src = [
        r for r in (*marked, *likely_safe) if "REPO_UNAVAILABLE" in _as_list(r.flags)
    ]
    unavailable_rows = [_row_public(r) for r in _ranked(unavailable_src)]

    # Dedicated tabs for "can't verify" cases: keep Flagged/Safe for scored history.
    def _keep_main(row: dict) -> bool:
        flags = row.get("flags") or []
        return "NO_GITHUB" not in flags and "REPO_UNAVAILABLE" not in flags

    safe_rows = [r for r in safe_rows if _keep_main(r)]
    marked_rows = [r for r in marked_rows if _keep_main(r)]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "meta": {
            "hackathon_url": _safe_url(meta.get("hackathon_url")),
            "window_local": meta.get("window_local"),
            "start": meta.get("start"),
            "end": meta.get("end"),
            "preset": meta.get("preset"),
            "min_score": meta.get("min_score"),
            "source": meta.get("source"),
        },
        "summary": summary,
        "flag_hints": FLAG_HINTS,
        "marked": marked_rows,
        "likely_safe": safe_rows,
        "no_github": no_github_rows,
        "unavailable": unavailable_rows,
    }
    data_json = json.dumps(payload, ensure_ascii=False)
    # Prevent breaking out of the script tag.
    data_json = data_json.replace("</", "<\\/")

    hack_label = hackathon_display_name(str(meta.get("hackathon_url") or ""))
    page_heading = f"{hack_label} Devpost Vibe Checker"
    title = html.escape(page_heading)
    heading_html = html.escape(page_heading)
    csv_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", hack_label.lower()).strip("-") or "review"
    csv_filename = f"{csv_name}-vibe-check.csv"

    # Public CSV beside the HTML (no emails) for static download + Netlify publish.
    _write_public_csv(
        path.parent / csv_filename,
        marked_rows,
        no_github_rows,
        unavailable_rows,
        safe_rows,
    )

    # Visual tokens: clean dark surface, amber/red danger scale, accessible contrast.
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="color-scheme" content="dark"/>
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet"/>
<style>
:root {{
  --bg: #0c0c0e;
  --bg-elevated: #131316;
  --card: #16161a;
  --card-hover: #1c1c22;
  --ink: #f0f0f4;
  --muted: #8b8b9a;
  --soft: #5a5a6e;
  --line: rgba(255,255,255,0.08);
  --line-strong: rgba(255,255,255,0.14);

  --brand: #7c7ef7;
  --brand-soft: rgba(124,126,247,0.12);
  --brand-dim: rgba(124,126,247,0.06);

  --danger: #f87171;
  --danger-soft: rgba(248,113,113,0.12);
  --warn: #fb923c;
  --warn-soft: rgba(251,146,60,0.12);
  --safe: #4ade80;
  --safe-soft: rgba(74,222,128,0.10);

  --font: Inter, ui-sans-serif, system-ui, sans-serif;
  --mono: "DM Mono", ui-monospace, Menlo, Consolas, monospace;
  --r: 0.5rem;
  --r-lg: 0.75rem;
}}
*, *::before, *::after {{ box-sizing: border-box; }}
body {{
  margin: 0;
  min-height: 100vh;
  font-family: var(--font);
  color: var(--ink);
  background: var(--bg);
  line-height: 1.5;
  font-feature-settings: "cv01" on, "cv03" on, "cv04" on, "cv11" on;
  -webkit-font-smoothing: antialiased;
}}

/* Header */
header {{
  padding: 2rem 1.5rem 1.25rem;
  max-width: 1100px;
  margin: 0 auto;
  border-bottom: 1px solid var(--line);
}}
header h1 {{
  font-size: clamp(1.4rem, 2.5vw, 1.85rem);
  margin: 0 0 0.5rem;
  letter-spacing: -0.025em;
  font-weight: 700;
  color: var(--ink);
}}
.badge-warn {{
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  background: var(--warn-soft);
  color: var(--warn);
  border: 1px solid rgba(251,146,60,0.28);
  font-weight: 600;
  font-size: 0.78rem;
  padding: 0.3rem 0.6rem;
  border-radius: 999px;
  margin-bottom: 0.75rem;
  letter-spacing: 0.01em;
}}
.badge-warn svg {{ width: 0.95rem; height: 0.95rem; flex-shrink: 0; }}
.meta-line {{
  font-size: 0.83rem;
  color: var(--muted);
  margin-bottom: 0.2rem;
}}
.meta-line a {{ color: var(--brand); text-decoration: none; }}
.meta-line a:hover {{ text-decoration: underline; }}

/* Summary pills */
.summary {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem 0.5rem;
  margin-top: 0.85rem;
}}
.pill {{
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.28rem 0.6rem;
  border-radius: 999px;
  background: var(--bg-elevated);
  border: 1px solid var(--line-strong);
  font-size: 0.76rem;
  color: var(--muted);
}}
.pill strong {{ color: var(--ink); font-weight: 600; }}
.pill.flagged {{ background: var(--danger-soft); border-color: rgba(248,113,113,0.22); color: var(--danger); }}
.pill.flagged strong {{ color: var(--danger); }}

/* Controls */
.controls {{
  max-width: 1100px;
  margin: 0 auto;
  padding: 1rem 1.5rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: center;
}}
.controls input[type="search"] {{
  flex: 1 1 200px;
  min-width: 160px;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--line-strong);
  border-radius: var(--r);
  background: var(--card);
  color: var(--ink);
  font: inherit;
  font-size: 0.88rem;
  transition: border-color 0.15s, box-shadow 0.15s;
}}
.controls input[type="search"]::placeholder {{ color: var(--soft); }}
.controls input[type="search"]:focus {{
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 3px rgba(124,126,247,0.18);
}}
.btn {{
  border: 1px solid var(--line-strong);
  background: var(--card);
  color: var(--muted);
  padding: 0.45rem 0.8rem;
  border-radius: var(--r);
  font: inherit;
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
  text-decoration: none;
  white-space: nowrap;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}}
.btn:hover {{ background: var(--card-hover); border-color: var(--line-strong); color: var(--ink); }}

/* Segmented control */
.seg {{
  display: inline-flex;
  border: 1px solid var(--line-strong);
  border-radius: var(--r);
  overflow: hidden;
  background: var(--card);
  flex-shrink: 0;
}}
.seg button {{
  border: 0;
  border-right: 1px solid var(--line);
  background: transparent;
  padding: 0.45rem 0.75rem;
  font: inherit;
  font-size: 0.83rem;
  font-weight: 500;
  cursor: pointer;
  color: var(--muted);
  transition: background 0.12s, color 0.12s;
  white-space: nowrap;
}}
.seg button:last-child {{ border-right: 0; }}
.seg button:hover {{ background: var(--card-hover); color: var(--ink); }}
.seg button.active {{ background: var(--brand); color: #fff; font-weight: 600; }}
.seg button.active:hover {{ background: var(--brand); }}

/* Result count */
.result-count {{
  max-width: 1100px;
  margin: 0 auto;
  padding: 0 1.5rem 0.5rem;
  font-size: 0.8rem;
  color: var(--soft);
}}

/* Cards */
main {{
  max-width: 1100px;
  margin: 0 auto;
  padding: 0 1.5rem 3rem;
  display: grid;
  gap: 0.6rem;
}}
.card {{
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  padding: 0.9rem 1rem;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0 0.9rem;
  transition: border-color 0.15s, background 0.15s;
  position: relative;
}}
.card:hover {{ border-color: var(--line-strong); background: var(--card-hover); }}

/* Score column */
.score-col {{
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  padding-top: 0.1rem;
  gap: 0.3rem;
  min-width: 3.2rem;
}}
.score {{
  font-family: var(--mono);
  font-weight: 500;
  font-size: 1.35rem;
  line-height: 1;
  letter-spacing: -0.03em;
  color: var(--warn);
}}
.score.high {{ color: var(--danger); }}
.score.low  {{ color: var(--muted); }}
.score.safe {{ color: var(--safe); }}
.score-label {{
  font-size: 0.6rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--soft);
}}

/* Content column */
.content {{ display: grid; gap: 0.45rem; min-width: 0; }}
.card-top {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem 0.75rem;
  align-items: baseline;
  justify-content: space-between;
}}
.project-title {{
  font-weight: 600;
  font-size: 0.98rem;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 55ch;
}}
.project-title a {{ color: inherit; text-decoration: none; }}
.project-title a:hover {{ color: var(--brand); }}
.card-links {{ display: flex; gap: 0.5rem; flex-shrink: 0; }}
.link-btn {{
  font-size: 0.76rem;
  font-weight: 500;
  padding: 0.2rem 0.5rem;
  border-radius: 999px;
  border: 1px solid var(--line-strong);
  color: var(--muted);
  text-decoration: none;
  transition: border-color 0.12s, color 0.12s;
  white-space: nowrap;
}}
.link-btn:hover {{ border-color: var(--brand); color: var(--brand); }}

/* Flags */
.flags {{ display: flex; flex-wrap: wrap; gap: 0.3rem; }}
.flag {{
  font-family: var(--mono);
  font-size: 0.67rem;
  padding: 0.18rem 0.45rem;
  border-radius: 999px;
  background: var(--brand-dim);
  color: var(--brand);
  border: 1px solid rgba(124,126,247,0.2);
  letter-spacing: 0.02em;
}}
.flag.priority {{
  background: var(--danger-soft);
  color: var(--danger);
  border-color: rgba(248,113,113,0.25);
}}

/* Stats bar */
.stats-bar {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem 0.75rem;
  font-family: var(--mono);
  font-size: 0.72rem;
  color: var(--soft);
}}
.stats-bar span {{ display: inline-flex; align-items: center; gap: 0.2rem; }}
.stats-bar .stat-val {{ color: var(--muted); }}
.stats-bar .stat-val.accent {{ color: var(--warn); }}

/* Reasons */
.reasons {{
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 0.15rem;
}}
.reasons li {{
  font-size: 0.83rem;
  color: var(--muted);
  padding-left: 1rem;
  position: relative;
}}
.reasons li::before {{
  content: "·";
  position: absolute;
  left: 0.25rem;
  color: var(--soft);
}}

/* Hint / AI summary */
.hint {{
  font-size: 0.8rem;
  color: var(--muted);
  border-left: 2px solid var(--line-strong);
  padding: 0.3rem 0.6rem;
  background: var(--bg-elevated);
  border-radius: 0 var(--r) var(--r) 0;
}}
.hint.ai {{ border-left-color: var(--brand); }}
.hint-label {{
  font-weight: 600;
  font-size: 0.7rem;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--brand);
  margin-right: 0.4rem;
}}

/* Empty state */
.empty {{
  text-align: center;
  color: var(--muted);
  padding: 3rem 1rem;
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-lg);
  background: var(--bg-elevated);
  font-size: 0.9rem;
}}
footer {{
  max-width: 1100px;
  margin: 0 auto;
  padding: 0 1.5rem 2.5rem;
  font-size: 0.75rem;
  color: var(--soft);
  border-top: 1px solid var(--line);
  padding-top: 1rem;
}}
</style>
</head>
<body>
<header>
  <div class="badge-warn">
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 3.2 2.8 19.5a1 1 0 0 0 .87 1.5h16.66a1 1 0 0 0 .87-1.5L12 3.2Z"
        stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/>
      <path d="M12 9v5.5" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>
      <circle cx="12" cy="17.2" r="1" fill="currentColor"/>
    </svg>
    Review queue, not an auto-DQ list. Every flag needs a human decision.
  </div>
  <h1>{heading_html}</h1>
  <div id="meta"></div>
  <div class="summary" id="summary"></div>
</header>
<div class="controls">
  <div class="seg" id="bucketSeg">
    <button type="button" data-bucket="marked" class="active">Flagged</button>
    <button type="button" data-bucket="no_github">No GitHub</button>
    <button type="button" data-bucket="unavailable">Unavailable</button>
    <button type="button" data-bucket="safe">Likely safe</button>
    <button type="button" data-bucket="all">All</button>
  </div>
  <input type="search" id="q" placeholder="Search project, flag, repo…" autocomplete="off" spellcheck="false"/>
  <button type="button" class="btn" id="dlCsv">↓ CSV</button>
</div>
<div class="result-count" id="resultCount"></div>
<main id="list"></main>
<footer>
  Team emails are excluded from this page. Safe to share with co-reviewers.
</footer>
<script id="payload" type="application/json">{data_json}</script>
<script>
const DATA = JSON.parse(document.getElementById("payload").textContent);
const CSV_NAME = {json.dumps(csv_filename)};
const listEl = document.getElementById("list");
const qEl = document.getElementById("q");
const countEl = document.getElementById("resultCount");
let bucket = "marked";

function esc(s) {{
  return String(s ?? "").replace(/[&<>"']/g, c => ({{
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }})[c]);
}}

// Meta
const m = DATA.meta || {{}};
const metaEl = document.getElementById("meta");
const metaParts = [];
if (m.window_local) metaParts.push(`<span class="meta-line">Window: <strong style="color:var(--ink)">${{esc(m.window_local)}}</strong></span>`);
if (m.hackathon_url) metaParts.push(`<span class="meta-line">Hackathon: <a href="${{esc(m.hackathon_url)}}" target="_blank" rel="noopener">${{esc(m.hackathon_url)}}</a></span>`);
metaParts.push(`<span class="meta-line">Preset: <strong style="color:var(--ink)">${{esc(m.preset)}}</strong> · Min score: <strong style="color:var(--ink)">${{esc(m.min_score)}}</strong> · Generated ${{new Date(DATA.generated_at).toLocaleString()}}</span>`);
metaEl.innerHTML = `<div style="display:flex;flex-direction:column;gap:0.15rem;margin-bottom:0.1rem">${{metaParts.join("")}}</div>`;

// Summary pills: counts come from the tabs themselves so pills and tabs always agree
// (the incoming summary counts do not account for rows moved into No GitHub / Unavailable).
const pillDefs = [
  ["Loaded", allRows().length, ""],
  ["Flagged", (DATA.marked || []).length, "flagged"],
  ["Safe", (DATA.likely_safe || []).length, ""],
  ["No GitHub", (DATA.no_github || []).length, ""],
  ["Unavailable", (DATA.unavailable || []).length, ""],
];
document.getElementById("summary").innerHTML = pillDefs
  .map(([label, n, cls]) => `<span class="pill ${{cls}}">${{label}}: <strong>${{n}}</strong></span>`)
  .join("");

// Update tab labels with counts
const ngBtn = document.querySelector('#bucketSeg button[data-bucket="no_github"]');
if (ngBtn) ngBtn.textContent = `No GitHub (${{(DATA.no_github||[]).length}})`;
const uvBtn = document.querySelector('#bucketSeg button[data-bucket="unavailable"]');
if (uvBtn) uvBtn.textContent = `Unavailable (${{(DATA.unavailable||[]).length}})`;

document.getElementById("bucketSeg").addEventListener("click", e => {{
  const btn = e.target.closest("button[data-bucket]");
  if (!btn) return;
  bucket = btn.dataset.bucket;
  for (const b of document.querySelectorAll("#bucketSeg button"))
    b.classList.toggle("active", b === btn);
  render();
}});
qEl.addEventListener("input", render);

function allRows() {{
  const seen = new Set();
  const out = [];
  for (const r of [
    ...(DATA.marked||[]),
    ...(DATA.no_github||[]),
    ...(DATA.unavailable||[]),
    ...(DATA.likely_safe||[]),
  ]) {{
    const key = r.project_url || r.project_title;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(r);
  }}
  return out.sort((a,b) =>
    ((b.suspicion_score ?? 0) - (a.suspicion_score ?? 0)) ||
    String(a.project_title).localeCompare(String(b.project_title))
  );
}}
function rowsForBucket() {{
  if (bucket === "marked")     return DATA.marked || [];
  if (bucket === "no_github")  return DATA.no_github || [];
  if (bucket === "unavailable") return DATA.unavailable || [];
  if (bucket === "safe")       return DATA.likely_safe || [];
  return allRows();
}}
function matches(row, q) {{
  if (!q) return true;
  const blob = [
    row.project_title, row.primary_repo,
    ...(row.flags||[]), ...(row.reasons||[]),
    row.gemini_verdict, row.gemini_summary
  ].join(" ").toLowerCase();
  return blob.includes(q);
}}

function scoreClass(score, bucket) {{
  if (bucket === "likely_safe" || bucket === "safe") return "safe";
  if (score >= 80) return "high";
  if (score <= 15) return "low";
  return "";
}}

function csvCell(v) {{
  let s = String(v ?? "");
  // Neutralize spreadsheet formula injection from user-controlled text.
  if (/^[=+\\-@\\t\\r]/.test(s)) s = "'" + s;
  if (/[",\\n\\r]/.test(s)) return '"' + s.replace(/"/g,'""') + '"';
  return s;
}}
function downloadCsv() {{
  const cols = ["suspicion_score","bucket","project_title","project_url","primary_repo","flags","reasons","total_commits","in_window_commits","pre_start_commits","post_end_commits"];
  const lines = [cols.join(",")];
  for (const r of allRows()) {{
    lines.push([
      r.suspicion_score, r.bucket, r.project_title, r.project_url, r.primary_repo,
      (r.flags||[]).join("|"), (r.reasons||[]).join(" | "),
      r.total_commits, r.in_window_commits, r.pre_start_commits, r.post_end_commits
    ].map(csvCell).join(","));
  }}
  const blob = new Blob([lines.join("\\n")], {{ type:"text/csv;charset=utf-8" }});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = CSV_NAME;
  a.click();
  URL.revokeObjectURL(a.href);
}}
document.getElementById("dlCsv").addEventListener("click", downloadCsv);

function render() {{
  const q = qEl.value.trim().toLowerCase();
  const rows = rowsForBucket().filter(r => matches(r, q));

  const total = rowsForBucket().length;
  countEl.textContent = q
    ? `${{rows.length}} of ${{total}} results`
    : `${{total}} project${{total !== 1 ? "s" : ""}}`;

  if (!rows.length) {{
    listEl.innerHTML = `<div class="empty">No projects match.</div>`;
    return;
  }}

  const PRIORITY_FLAGS = new Set(["PRE_START_REVIEW","PRE_START_COMMITS","ALL_COMMITS_BEFORE_START","ALL_COMMITS_OUTSIDE_WINDOW","MEANINGFUL_WORK_BEFORE_START","BULK_DUMP"]);

  listEl.innerHTML = rows.map(r => {{
    const sc = scoreClass(r.suspicion_score, r.bucket);
    const flags = (r.flags||[]).map(f =>
      `<span class="flag${{PRIORITY_FLAGS.has(f) ? " priority" : ""}}">${{esc(f)}}</span>`
    ).join("");

    const reasons = (r.reasons||[]).slice(0,3)
      .map(x => `<li>${{esc(x)}}</li>`).join("");

    const projLink = r.project_url
      ? `<a class="link-btn" href="${{esc(r.project_url)}}" target="_blank" rel="noopener">Devpost ↗</a>` : "";
    const repoLink = r.primary_repo_url
      ? `<a class="link-btn" href="${{esc(r.primary_repo_url)}}" target="_blank" rel="noopener">GitHub ↗</a>` : "";

    const inW = r.in_window_commits ?? 0;
    const pre = r.pre_start_commits ?? 0;
    const post = r.post_end_commits ?? 0;
    const totalCommits = r.total_commits ?? "-";

    const statAccent = pre > 0 ? " accent" : "";

    const hintLine = (() => {{
      const hints = (r.flags||[]).map(f => DATA.flag_hints[f]).filter(Boolean);
      if (!hints.length) return "";
      return `<div class="hint">${{esc(hints[0])}}</div>`;
    }})();

    const aiLine = r.gemini_summary
      ? `<div class="hint ai"><span class="hint-label">AI (${{esc(r.gemini_verdict)}})</span>${{esc(r.gemini_summary)}}</div>`
      : "";

    return `<article class="card">
  <div class="score-col">
    <span class="score ${{sc}}">${{r.suspicion_score ?? 0}}</span>
    <span class="score-label">score</span>
  </div>
  <div class="content">
    <div class="card-top">
      <span class="project-title">${{r.project_url
        ? `<a href="${{esc(r.project_url)}}" target="_blank" rel="noopener">${{esc(r.project_title)}}</a>`
        : esc(r.project_title)}}</span>
      <div class="card-links">${{projLink}}${{repoLink}}</div>
    </div>
    <div class="flags">${{flags || '<span class="flag">none</span>'}}</div>
    <div class="stats-bar">
      <span>total <span class="stat-val">${{totalCommits}}</span></span>
      <span>in-window <span class="stat-val">${{inW}}</span></span>
      <span>pre-start <span class="stat-val${{statAccent}}">${{pre}}</span></span>
      <span>post-end <span class="stat-val">${{post}}</span></span>
      ${{r.primary_repo ? `<span style="color:var(--soft);font-size:0.68rem">${{esc(String(r.primary_repo).replace(/^https?:\\/\\/github\\.com\\//i, ""))}}</span>` : ""}}
    </div>
    ${{reasons ? `<ul class="reasons">${{reasons}}</ul>` : ""}}
    ${{hintLine}}
    ${{aiLine}}
  </div>
</article>`;
  }}).join("");
}}
render();
</script>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")
    return path


_CSV_COLS = (
    "suspicion_score",
    "bucket",
    "project_title",
    "project_url",
    "primary_repo",
    "flags",
    "reasons",
    "total_commits",
    "in_window_commits",
    "pre_start_commits",
    "post_end_commits",
)


def _write_public_csv(
    path: Path,
    marked: list[dict],
    no_github: list[dict],
    unavailable: list[dict],
    safe: list[dict],
) -> None:
    """Write email-free CSV next to index.html for download / publish."""
    seen: set[str] = set()
    rows: list[dict] = []
    for r in (*marked, *no_github, *unavailable, *safe):
        key = str(r.get("project_url") or r.get("project_title") or "")
        if key in seen:
            continue
        seen.add(key)
        rows.append(r)
    rows.sort(
        key=lambda r: (-int(r.get("suspicion_score") or 0), str(r.get("project_title") or ""))
    )
    buf = StringIO()
    w = csv.DictWriter(buf, fieldnames=list(_CSV_COLS), extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(
            {
                "suspicion_score": r.get("suspicion_score"),
                "bucket": r.get("bucket"),
                "project_title": _csv_safe(r.get("project_title")),
                "project_url": r.get("project_url"),
                "primary_repo": _csv_safe(r.get("primary_repo")),
                "flags": "|".join(r.get("flags") or []),
                "reasons": _csv_safe(" | ".join(r.get("reasons") or [])),
                "total_commits": r.get("total_commits"),
                "in_window_commits": r.get("in_window_commits"),
                "pre_start_commits": r.get("pre_start_commits"),
                "post_end_commits": r.get("post_end_commits"),
            }
        )
    path.write_text(buf.getvalue(), encoding="utf-8")
