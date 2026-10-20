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
    """https://stormhacks2026.devpost.com → StormHacks2026"""
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


def _row_public(r: VibeResult) -> dict:
    """Shareable fields only; no team/author emails."""
    return {
        "rank": r.rank,
        "bucket": r.bucket,
        "suspicion_score": r.suspicion_score,
        "flags": list(r.flags),
        "reasons": list(r.reasons)[:6],
        "project_title": r.project_title,
        "project_url": r.project_url,
        "primary_repo": r.primary_repo,
        "primary_repo_url": github_repo_url(r.primary_repo) if r.primary_repo else "",
        "total_commits": r.total_commits,
        "in_window_commits": r.in_window_commits,
        "pre_start_commits": r.pre_start_commits,
        "post_end_commits": r.post_end_commits,
        "sample_messages": (r.sample_messages or "")[:400],
        "name_match_score": r.name_match_score,
        "relevance_hits": list(r.relevance_hits)[:8],
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
    no_github_src = [r for r in (*marked, *likely_safe) if "NO_GITHUB" in (r.flags or [])]
    no_github_rows = [_row_public(r) for r in _ranked(no_github_src)]
    unavailable_src = [r for r in (*marked, *likely_safe) if "REPO_UNAVAILABLE" in (r.flags or [])]
    unavailable_rows = [_row_public(r) for r in _ranked(unavailable_src)]

    # Dedicated tabs for "can't verify" cases — keep Flagged/Safe for scored history.
    def _keep_main(row: dict) -> bool:
        flags = row.get("flags") or []
        return "NO_GITHUB" not in flags and "REPO_UNAVAILABLE" not in flags

    safe_rows = [r for r in safe_rows if _keep_main(r)]
    marked_rows = [r for r in marked_rows if _keep_main(r)]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "meta": {
            "hackathon_url": meta.get("hackathon_url"),
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

    # Visual tokens: indigo accent + flat neutral dark surface (no gradients).
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
  --bg: #0a0a0a;
  --bg-elevated: #111111;
  --card: #171717;
  --card-hover: #1f1f1f;
  --ink: #f5f5f5;
  --muted: #a3a3a3;
  --line: rgba(82, 82, 82, 0.55);
  --brand: #6466f1;
  --brand-soft: rgba(100, 102, 241, 0.18);
  --brand-strong: #5046e5;
  --caution: #fb923c;
  --caution-soft: rgba(249, 115, 22, 0.16);
  --success: #22c55d;
  --font: Inter, ui-sans-serif, system-ui, sans-serif;
  --mono: "DM Mono", ui-monospace, Menlo, Consolas, monospace;
  --radius: 0.5rem;
  --shadow-inset: 0 1px 0 0 rgba(255,255,255,0.12) inset, 0 0 0 1px rgba(255,255,255,0.06) inset;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  min-height: 100vh;
  font-family: var(--font);
  color: var(--ink);
  background: var(--bg);
  line-height: 1.45;
  font-feature-settings: "cv01" on, "cv03" on, "cv04" on, "cv11" on;
}}
header {{
  padding: 1.75rem 1.25rem 1rem;
  max-width: 1100px;
  margin: 0 auto;
}}
header h1 {{
  font-size: clamp(1.55rem, 3vw, 2rem);
  margin: 0.35rem 0 0.45rem;
  letter-spacing: -0.02em;
  font-weight: 700;
}}
.banner {{
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  background: var(--caution-soft);
  color: var(--caution);
  border: 1px solid rgba(249, 115, 22, 0.35);
  font-weight: 600;
  font-size: 0.85rem;
  padding: 0.4rem 0.7rem;
  border-radius: var(--radius);
  margin-bottom: 0.85rem;
}}
.banner svg {{
  flex-shrink: 0;
  width: 1.1rem;
  height: 1.1rem;
}}
.meta, .summary {{
  color: var(--muted);
  font-size: 0.9rem;
}}
.meta a {{ color: var(--brand); }}
.summary {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem 0.65rem;
  margin-top: 0.9rem;
}}
.summary span {{
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.3rem 0.55rem;
  border-radius: 999px;
  background: var(--bg-elevated);
  border: 1px solid var(--line);
  font-size: 0.8rem;
}}
.summary span strong {{ color: var(--ink); font-weight: 600; }}
.controls {{
  max-width: 1100px;
  margin: 0 auto;
  padding: 0 1.25rem 1rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: center;
}}
.controls input[type="search"] {{
  flex: 1 1 220px;
  min-width: 180px;
  padding: 0.55rem 0.75rem;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--card);
  color: var(--ink);
  font: inherit;
}}
.controls input[type="search"]::placeholder {{ color: #737373; }}
.controls input[type="search"]:focus {{
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 2px rgba(100, 102, 241, 0.35);
}}
.btn-dl {{
  border: 1px solid var(--line);
  background: var(--card);
  color: var(--ink);
  padding: 0.5rem 0.85rem;
  border-radius: var(--radius);
  font: inherit;
  font-size: 0.9rem;
  font-weight: 500;
  cursor: pointer;
  text-decoration: none;
  white-space: nowrap;
}}
.btn-dl:hover {{ background: var(--card-hover); border-color: var(--brand); color: #fff; }}
.seg {{
  display: inline-flex;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
  background: var(--card);
}}
.seg button {{
  border: 0;
  background: transparent;
  padding: 0.5rem 0.8rem;
  font: inherit;
  font-size: 0.9rem;
  font-weight: 500;
  cursor: pointer;
  color: var(--muted);
}}
.seg button:hover {{ background: var(--card-hover); color: var(--ink); }}
.seg button.active {{
  background: var(--brand-strong);
  color: #fff;
  box-shadow: var(--shadow-inset);
}}
main {{
  max-width: 1100px;
  margin: 0 auto;
  padding: 0 1.25rem 3rem;
  display: grid;
  gap: 0.75rem;
}}
.card {{
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 0.65rem;
  padding: 0.95rem 1.05rem;
  display: grid;
  gap: 0.5rem;
  transition: border-color 0.15s ease, background 0.15s ease;
}}
.card:hover {{ border-color: rgba(100, 102, 241, 0.35); background: var(--card-hover); }}
.card.safe {{ opacity: 0.92; }}
.top {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 0.85rem;
  align-items: baseline;
  justify-content: space-between;
}}
.score {{
  font-family: var(--mono);
  font-weight: 500;
  font-size: 1.2rem;
  color: var(--caution);
  min-width: 2.5rem;
}}
.card.safe .score {{ color: var(--success); }}
.title {{
  font-weight: 600;
  font-size: 1.05rem;
}}
.title a {{ color: inherit; text-decoration: none; }}
.title a:hover {{ color: var(--brand); }}
.links a {{
  color: var(--brand);
  font-size: 0.88rem;
  font-weight: 500;
  margin-right: 0.85rem;
  text-decoration: none;
}}
.links a:hover {{ text-decoration: underline; }}
.flags {{ display: flex; flex-wrap: wrap; gap: 0.35rem; }}
.flag {{
  font-family: var(--mono);
  font-size: 0.7rem;
  padding: 0.22rem 0.5rem;
  border-radius: 999px;
  background: var(--brand-soft);
  color: #c7d2fe;
  border: 1px solid rgba(100, 102, 241, 0.28);
}}
.flag.priority {{
  background: var(--caution-soft);
  color: var(--caution);
  border-color: rgba(249, 115, 22, 0.35);
}}
.reasons {{ margin: 0; padding-left: 1.1rem; color: var(--muted); font-size: 0.9rem; }}
.stats {{
  font-family: var(--mono);
  font-size: 0.76rem;
  color: #737373;
}}
.hint {{
  font-size: 0.82rem;
  color: var(--muted);
  border-left: 3px solid var(--brand);
  padding: 0.35rem 0.65rem;
  background: var(--bg-elevated);
  border-radius: 0 0.35rem 0.35rem 0;
}}
.empty {{
  text-align: center;
  color: var(--muted);
  padding: 2.5rem 1rem;
  border: 1px dashed var(--line);
  border-radius: var(--radius);
  background: var(--bg-elevated);
}}
footer {{
  max-width: 1100px;
  margin: 0 auto;
  padding: 0 1.25rem 2.5rem;
  font-size: 0.8rem;
  color: #737373;
}}
</style>
</head>
<body>
<header>
  <div class="banner">
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 3.2 2.8 19.5a1 1 0 0 0 .87 1.5h16.66a1 1 0 0 0 .87-1.5L12 3.2Z"
        stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/>
      <path d="M12 9v5.5" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>
      <circle cx="12" cy="17.2" r="1" fill="currentColor"/>
    </svg>
    <span>Review queue, not an auto-DQ list. Every flag needs a human decision.</span>
  </div>
  <h1>{heading_html}</h1>
  <div class="meta" id="meta"></div>
  <div class="summary" id="summary"></div>
</header>
<div class="controls">
  <div class="seg" id="bucketSeg">
    <button type="button" data-bucket="marked" class="active">Flagged</button>
    <button type="button" data-bucket="no_github">Can't find GitHub</button>
    <button type="button" data-bucket="unavailable">Can't access repo</button>
    <button type="button" data-bucket="safe">Likely safe</button>
    <button type="button" data-bucket="all">All</button>
  </div>
  <input type="search" id="q" placeholder="Filter by project, flag, repo..." autocomplete="off"/>
  <button type="button" class="btn-dl" id="dlCsv">Download CSV</button>
</div>
<main id="list"></main>
<footer>
  Team emails are left out of this page. Share this link with co-reviewers.
</footer>
<script id="payload" type="application/json">{data_json}</script>
<script>
const DATA = JSON.parse(document.getElementById("payload").textContent);
const CSV_NAME = {json.dumps(csv_filename)};
const listEl = document.getElementById("list");
const qEl = document.getElementById("q");
let bucket = "marked";

function esc(s) {{
  return String(s ?? "").replace(/[&<>"']/g, c => ({{
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }})[c]);
}}

document.getElementById("meta").innerHTML = [
  DATA.meta.window_local ? `<div>Window: <strong>${{esc(DATA.meta.window_local)}}</strong></div>` : "",
  DATA.meta.hackathon_url ? `<div>Hackathon: <a href="${{esc(DATA.meta.hackathon_url)}}">${{esc(DATA.meta.hackathon_url)}}</a></div>` : "",
  `<div>Preset / min score: <strong>${{esc(DATA.meta.preset)}}</strong> / <strong>${{esc(DATA.meta.min_score)}}</strong> · Generated ${{esc(DATA.generated_at)}}</div>`,
].join("");

const s = DATA.summary || {{}};
const noGithubCount = (DATA.no_github || []).length;
const unavailableCount = (DATA.unavailable || []).length;
document.getElementById("summary").innerHTML = [
  ["projects_loaded","Loaded"],
  ["marked","Flagged"],
  ["likely_safe","Likely safe"],
  ["no_github","No GitHub"],
  ["unavailable","Unavailable"],
].map(([k,label]) => `<span>${{label}}: <strong>${{s[k] ?? 0}}</strong></span>`).join("");

const noGithubBtn = document.querySelector('#bucketSeg button[data-bucket="no_github"]');
if (noGithubBtn) {{
  noGithubBtn.textContent = noGithubCount
    ? `Can't find GitHub (${{noGithubCount}})`
    : "Can't find GitHub";
}}
const unavailableBtn = document.querySelector('#bucketSeg button[data-bucket="unavailable"]');
if (unavailableBtn) {{
  unavailableBtn.textContent = unavailableCount
    ? `Can't access repo (${{unavailableCount}})`
    : "Can't access repo";
}}

document.getElementById("bucketSeg").addEventListener("click", (e) => {{
  const btn = e.target.closest("button[data-bucket]");
  if (!btn) return;
  bucket = btn.dataset.bucket;
  for (const b of document.querySelectorAll("#bucketSeg button")) {{
    b.classList.toggle("active", b === btn);
  }}
  render();
}});
qEl.addEventListener("input", render);

function allRows() {{
  const seen = new Set();
  const out = [];
  for (const r of [
    ...(DATA.marked || []),
    ...(DATA.no_github || []),
    ...(DATA.unavailable || []),
    ...(DATA.likely_safe || []),
  ]) {{
    const key = r.project_url || r.project_title;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(r);
  }}
  return out.sort(
    (a,b) => (b.suspicion_score - a.suspicion_score)
      || String(a.project_title).localeCompare(String(b.project_title))
  );
}}

function rowsForBucket() {{
  if (bucket === "marked") return DATA.marked || [];
  if (bucket === "no_github") return DATA.no_github || [];
  if (bucket === "unavailable") return DATA.unavailable || [];
  if (bucket === "safe") return DATA.likely_safe || [];
  return allRows();
}}

function matches(row, q) {{
  if (!q) return true;
  const blob = [
    row.project_title, row.primary_repo, ...(row.flags||[]), ...(row.reasons||[]),
    row.gemini_verdict, row.gemini_summary
  ].join(" ").toLowerCase();
  return blob.includes(q);
}}

function csvCell(v) {{
  const s = String(v ?? "");
  if (/[",\\n\\r]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}}

function downloadCsv() {{
  const cols = [
    "suspicion_score","bucket","project_title","project_url","primary_repo",
    "flags","reasons","total_commits","in_window_commits","pre_start_commits",
    "post_end_commits"
  ];
  const lines = [cols.join(",")];
  for (const r of allRows()) {{
    lines.push([
      r.suspicion_score, r.bucket, r.project_title, r.project_url, r.primary_repo,
      (r.flags || []).join("|"), (r.reasons || []).join(" | "),
      r.total_commits, r.in_window_commits, r.pre_start_commits, r.post_end_commits
    ].map(csvCell).join(","));
  }}
  const blob = new Blob([lines.join("\\n")], {{ type: "text/csv;charset=utf-8" }});
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
  if (!rows.length) {{
    listEl.innerHTML = `<div class="empty">No projects match.</div>`;
    return;
  }}
    listEl.innerHTML = rows.map(r => {{
    const hints = (r.flags || [])
      .map(f => DATA.flag_hints[f])
      .filter(Boolean)
      .slice(0, 2);
    const flags = (r.flags || []).map(f =>
      `<span class="flag${{f.includes("PRE_START") ? " priority" : ""}}">${{esc(f)}}</span>`
    ).join("");
    const reasons = (r.reasons || []).slice(0, 3)
      .map(x => `<li>${{esc(x)}}</li>`).join("");
    const repo = r.primary_repo_url
      ? `<a href="${{esc(r.primary_repo_url)}}" target="_blank" rel="noopener">GitHub</a>`
      : "";
    const proj = r.project_url
      ? `<a href="${{esc(r.project_url)}}" target="_blank" rel="noopener">Devpost</a>`
      : "";
    const isMissingGh = (r.flags || []).includes("NO_GITHUB");
    return `<article class="card${{r.bucket === "likely_safe" && !isMissingGh ? " safe" : ""}}">
      <div class="top">
        <div><span class="score">${{esc(r.suspicion_score)}}</span>
        <span class="title">${{r.project_url ? `<a href="${{esc(r.project_url)}}" target="_blank" rel="noopener">${{esc(r.project_title)}}</a>` : esc(r.project_title)}}</span></div>
        <div class="links">${{proj}}${{repo}}</div>
      </div>
      <div class="flags">${{flags || '<span class="flag">-</span>'}}</div>
      <div class="stats">commits ${{r.total_commits ?? "-"}} · in-window ${{r.in_window_commits ?? 0}} · pre-start ${{r.pre_start_commits ?? 0}} · post-end ${{r.post_end_commits ?? 0}}${{r.primary_repo ? " · " + esc(r.primary_repo) : ""}}</div>
      ${{reasons ? `<ul class="reasons">${{reasons}}</ul>` : ""}}
      ${{hints.length ? `<div class="hint">${{esc(hints[0])}}</div>` : ""}}
      ${{r.gemini_summary ? `<div class="hint">Gemini (${{esc(r.gemini_verdict)}}): ${{esc(r.gemini_summary)}}</div>` : ""}}
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
                "project_title": r.get("project_title"),
                "project_url": r.get("project_url"),
                "primary_repo": r.get("primary_repo"),
                "flags": "|".join(r.get("flags") or []),
                "reasons": " | ".join(r.get("reasons") or []),
                "total_commits": r.get("total_commits"),
                "in_window_commits": r.get("in_window_commits"),
                "pre_start_commits": r.get("pre_start_commits"),
                "post_end_commits": r.get("post_end_commits"),
            }
        )
    path.write_text(buf.getvalue(), encoding="utf-8")
