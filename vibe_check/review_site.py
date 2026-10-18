"""Generate a shareable static HTML review queue from run results."""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from pathlib import Path

from .models import VibeResult, github_repo_url

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
    no_github_src = [
        r
        for r in (*marked, *likely_safe)
        if "NO_GITHUB" in (r.flags or [])
    ]
    no_github_rows = [_row_public(r) for r in _ranked(no_github_src)]
    # Keep the Likely safe tab for repos we could score; missing GitHub has its own tab.
    safe_rows = [r for r in safe_rows if "NO_GITHUB" not in (r.get("flags") or [])]
    marked_rows = [r for r in marked_rows if "NO_GITHUB" not in (r.get("flags") or [])]
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
    }
    data_json = json.dumps(payload, ensure_ascii=False)
    # Prevent breaking out of the script tag.
    data_json = data_json.replace("</", "<\\/")

    title = html.escape(
        f"Vibe check | {meta.get('hackathon_url') or 'hackathon'}"
    )
    # Visual tokens aligned with SFU Surge hacker-portal (globals.css brand/neutral).
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
  --brand-deep: #1d1b4b;
  --caution: #fb923c;
  --caution-soft: rgba(249, 115, 22, 0.16);
  --success: #22c55d;
  --success-soft: rgba(34, 197, 94, 0.14);
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
  background:
    radial-gradient(900px 420px at 8% -8%, rgba(80, 70, 229, 0.22) 0%, transparent 55%),
    radial-gradient(700px 360px at 100% 0%, rgba(29, 27, 75, 0.55) 0%, transparent 50%),
    var(--bg);
  line-height: 1.45;
  font-feature-settings: "cv01" on, "cv03" on, "cv04" on, "cv11" on;
}}
header {{
  padding: 1.75rem 1.25rem 1rem;
  max-width: 1100px;
  margin: 0 auto;
}}
.eyebrow {{
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--brand);
  margin-bottom: 0.65rem;
}}
header h1 {{
  font-size: clamp(1.55rem, 3vw, 2rem);
  margin: 0 0 0.45rem;
  letter-spacing: -0.02em;
  font-weight: 700;
}}
.banner {{
  display: inline-block;
  background: var(--caution-soft);
  color: var(--caution);
  border: 1px solid rgba(249, 115, 22, 0.35);
  font-weight: 600;
  font-size: 0.85rem;
  padding: 0.4rem 0.7rem;
  border-radius: var(--radius);
  margin-bottom: 0.85rem;
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
  padding-left: 0.65rem;
  background: linear-gradient(90deg, var(--brand-deep), transparent 85%);
  border-radius: 0 0.35rem 0.35rem 0;
  padding-top: 0.35rem;
  padding-bottom: 0.35rem;
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
  <div class="eyebrow">SFU Surge</div>
  <div class="banner">Review queue, not an auto-DQ list. Every flag needs a human decision.</div>
  <h1>Devpost Vibe Check</h1>
  <div class="meta" id="meta"></div>
  <div class="summary" id="summary"></div>
</header>
<div class="controls">
  <div class="seg" id="bucketSeg">
    <button type="button" data-bucket="marked" class="active">Flagged</button>
    <button type="button" data-bucket="no_github">Can't find GitHub</button>
    <button type="button" data-bucket="safe">Likely safe</button>
    <button type="button" data-bucket="all">All</button>
  </div>
  <input type="search" id="q" placeholder="Filter by project, flag, repo..." autocomplete="off"/>
</div>
<main id="list"></main>
<footer>
  Generated by Devpost Vibe Check. Team emails are left out of this page;
  use marked.csv for full fields. Share the link or this file with co-reviewers.
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
const noGithubCount = (DATA.no_github || []).length;
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
