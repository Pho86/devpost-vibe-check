"""Generate a shareable static HTML review queue from run results."""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from pathlib import Path

from .models import VibeResult, github_repo_url

# Short organizer hints — same philosophy as README (look, don't auto-DQ).
FLAG_HINTS: dict[str, str] = {
    "PRE_START_REVIEW": "Priority look — confirm timezone, repo, or reused homework.",
    "PRE_START_COMMITS": "Commits before start — verify window and linked repo.",
    "ALL_COMMITS_BEFORE_START": "Entire history before start — wrong repo or prebuilt?",
    "MEANINGFUL_WORK_BEFORE_START": "Non-trivial work before hacking started.",
    "LOW_COMMITS": "Very few commits — possible zip dump or unfinished work.",
    "BULK_DUMP": "Tiny history + large tree — check for a bulk upload.",
    "ALL_COMMITS_OUTSIDE_WINDOW": "No in-window commits — wrong repo or outside event?",
    "NO_PROJECT_RELEVANCE": "Name/README don't match Devpost title — confirm link.",
    "REPO_UNAVAILABLE": "Private/404 — ask the team for access; not guilt.",
    "TEAM_NOT_ACCEPTED": "Team emails missing from accepted list (already flagged).",
    "TEAM_NOT_CHECKED_IN": "Matched emails not checked in day-of (already flagged).",
    "GEMINI_SUSPICIOUS": "LLM second opinion only — read summary, decide yourself.",
    "NO_GITHUB": "No GitHub linked — may be hardware/Figma or missing link.",
    "LIKELY_NONCODE": "Hardware / Figma / no-code signals — low commits may be fine.",
}


def _row_public(r: VibeResult) -> dict:
    """Shareable fields only — no team/author emails."""
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
    Emails and other PII stay in CSV/JSON only — not in this page.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    from .report import _ranked

    marked_rows = [_row_public(r) for r in _ranked(marked)]
    safe_rows = [_row_public(r) for r in _ranked(likely_safe)]
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
    }
    data_json = json.dumps(payload, ensure_ascii=False)
    # Prevent breaking out of the script tag.
    data_json = data_json.replace("</", "<\\/")

    title = html.escape(
        f"Vibe check — {meta.get('hackathon_url') or 'hackathon'}"
    )
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>
:root {{
  --bg: #f6f3ee;
  --ink: #1a1f1c;
  --muted: #5c6560;
  --line: #d9d2c5;
  --card: #fffdf8;
  --accent: #0b6e4f;
  --accent-soft: #d8efe6;
  --warn: #9a3412;
  --warn-soft: #ffedd5;
  --ok: #3f6212;
  --ok-soft: #ecfccb;
  --font: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, Consolas, monospace;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: var(--font);
  color: var(--ink);
  background:
    radial-gradient(1200px 500px at 10% -10%, #e7f2ec 0%, transparent 55%),
    radial-gradient(900px 400px at 100% 0%, #f3e8d8 0%, transparent 50%),
    var(--bg);
  line-height: 1.45;
}}
header {{
  padding: 1.75rem 1.25rem 1rem;
  max-width: 1100px;
  margin: 0 auto;
}}
header h1 {{
  font-size: clamp(1.6rem, 3vw, 2.1rem);
  margin: 0 0 0.35rem;
  letter-spacing: -0.02em;
}}
.banner {{
  display: inline-block;
  background: var(--warn-soft);
  color: var(--warn);
  font-weight: 600;
  font-size: 0.85rem;
  padding: 0.35rem 0.65rem;
  border-radius: 4px;
  margin-bottom: 0.75rem;
}}
.meta, .summary {{
  color: var(--muted);
  font-size: 0.92rem;
}}
.summary {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1rem;
  margin-top: 0.75rem;
}}
.summary span strong {{ color: var(--ink); }}
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
  border-radius: 6px;
  background: var(--card);
  font: inherit;
}}
.seg {{
  display: inline-flex;
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow: hidden;
  background: var(--card);
}}
.seg button {{
  border: 0;
  background: transparent;
  padding: 0.5rem 0.75rem;
  font: inherit;
  cursor: pointer;
  color: var(--muted);
}}
.seg button.active {{
  background: var(--accent);
  color: #fff;
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
  border-radius: 10px;
  padding: 0.9rem 1rem;
  display: grid;
  gap: 0.45rem;
}}
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
  font-weight: 700;
  font-size: 1.25rem;
  color: var(--warn);
  min-width: 2.5rem;
}}
.card.safe .score {{ color: var(--ok); }}
.title {{
  font-weight: 650;
  font-size: 1.05rem;
}}
.title a {{ color: inherit; text-decoration: none; border-bottom: 1px solid transparent; }}
.title a:hover {{ border-bottom-color: var(--accent); color: var(--accent); }}
.links a {{
  color: var(--accent);
  font-size: 0.88rem;
  margin-right: 0.85rem;
}}
.flags {{ display: flex; flex-wrap: wrap; gap: 0.35rem; }}
.flag {{
  font-family: var(--mono);
  font-size: 0.72rem;
  padding: 0.2rem 0.45rem;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
}}
.flag.priority {{ background: var(--warn-soft); color: var(--warn); }}
.reasons {{ margin: 0; padding-left: 1.1rem; color: var(--muted); font-size: 0.9rem; }}
.stats {{
  font-family: var(--mono);
  font-size: 0.78rem;
  color: var(--muted);
}}
.hint {{
  font-size: 0.82rem;
  color: var(--muted);
  border-left: 3px solid var(--line);
  padding-left: 0.65rem;
}}
.empty {{
  text-align: center;
  color: var(--muted);
  padding: 2rem 1rem;
}}
footer {{
  max-width: 1100px;
  margin: 0 auto;
  padding: 0 1.25rem 2rem;
  font-size: 0.8rem;
  color: var(--muted);
}}
</style>
</head>
<body>
<header>
  <div class="banner">Review queue — not an auto-DQ list. Every flag needs a human decision.</div>
  <h1>Devpost Vibe Check</h1>
  <div class="meta" id="meta"></div>
  <div class="summary" id="summary"></div>
</header>
<div class="controls">
  <div class="seg" id="bucketSeg">
    <button type="button" data-bucket="marked" class="active">Flagged</button>
    <button type="button" data-bucket="all">All</button>
    <button type="button" data-bucket="safe">Likely safe</button>
  </div>
  <input type="search" id="q" placeholder="Filter by project, flag, repo…" autocomplete="off"/>
</div>
<main id="list"></main>
<footer>
  Generated locally by Devpost Vibe Check. Team emails are omitted from this page —
  use marked.csv for full fields. Share this file with co-reviewers (Drive, zip, static host).
</footer>
<script id="payload" type="application/json">{data_json}</script>
<script>
const DATA = JSON.parse(document.getElementById("payload").textContent);
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
document.getElementById("summary").innerHTML = [
  ["projects_loaded","Loaded"],
  ["marked","Flagged"],
  ["likely_safe","Likely safe"],
  ["no_github","No GitHub"],
  ["unavailable","Unavailable"],
].map(([k,label]) => `<span>${{label}}: <strong>${{s[k] ?? 0}}</strong></span>`).join("");

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

function rowsForBucket() {{
  if (bucket === "marked") return DATA.marked || [];
  if (bucket === "safe") return DATA.likely_safe || [];
  return [...(DATA.marked || []), ...(DATA.likely_safe || [])]
    .sort((a,b) => (b.suspicion_score - a.suspicion_score) || String(a.project_title).localeCompare(String(b.project_title)));
}}

function matches(row, q) {{
  if (!q) return true;
  const blob = [
    row.project_title, row.primary_repo, ...(row.flags||[]), ...(row.reasons||[]),
    row.gemini_verdict, row.gemini_summary
  ].join(" ").toLowerCase();
  return blob.includes(q);
}}

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
    return `<article class="card${{r.bucket === "likely_safe" ? " safe" : ""}}">
      <div class="top">
        <div><span class="score">${{esc(r.suspicion_score)}}</span>
        <span class="title">${{r.project_url ? `<a href="${{esc(r.project_url)}}" target="_blank" rel="noopener">${{esc(r.project_title)}}</a>` : esc(r.project_title)}}</span></div>
        <div class="links">${{proj}}${{repo}}</div>
      </div>
      <div class="flags">${{flags || '<span class="flag">—</span>'}}</div>
      <div class="stats">commits ${{r.total_commits ?? "—"}} · in-window ${{r.in_window_commits ?? 0}} · pre-start ${{r.pre_start_commits ?? 0}} · post-end ${{r.post_end_commits ?? 0}}${{r.primary_repo ? " · " + esc(r.primary_repo) : ""}}</div>
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
