# Devpost Vibe Check

Load a Devpost **organizer projects export**, follow each team to GitHub, and **rank projects by how suspicious their commit history looks**.

Works with **any hackathon hosted on Devpost**. Built and tested against StormHacks-style exports.

Signals are for **manual review**, not auto-DQ — except where noted (pre-start is treated as an automatic mark).

## Pipeline

```text
1. CSV          organizer export (title, submission URL, Try-it-out links, team emails)
2. Devpost      submission pages → GitHub links + team member profile URLs
3. Team users   Devpost profiles → GitHub *usernames* (not every portfolio repo)
4. Discovery    match user repos by project name / in-window push
5. Git history  window scoring + project/hackathon name in commits/README
6. Gemini       optional second-pass (`--gemini`)
```

## Quick start

```bash
cd devpost-vibe-check
python -m venv .venv
source .venv/Scripts/activate   # or: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# set GITHUB_TOKEN=...
```

```bash
python -m vibe_check \
  --projects-csv path/to/projects-export.csv \
  --hackathon https://your-event.devpost.com \
  --start 2026-10-03T12:00:00 \
  --end 2026-10-04T12:00:00 \
  --tz America/Los_Angeles \
  --preset review
```

Naive `--start` / `--end` are interpreted in `--tz` (default `America/Los_Angeles`).

## Defaults that matter

- **Organizer CSV first** (galleries are often closed early)
- **Devpost + user scrape on** by default
- **One row per project** (multi-repo teams collapsed; worst *candidate* repo wins — name/relevance matched, not teammate portfolio junk)
- **Pre-start commits = automatic mark** (`PRE_START_DQ`)
- **Emails masked** in CSV/JSON by default (`a***@domain`); use `--include-pii` for full addresses
- **`output/` cleared** at the start of each run (unless `--resume`)

## Outputs (`output/`)

| File | Contents |
|------|----------|
| `marked.csv` / `.json` | Review queue (score ≥ preset) |
| `likely_safe.csv` / `.json` | Below threshold |
| `combined.csv` / `.json` | Everyone |
| `report.md` | Summary tables |
| `all_results.json` | Resume checkpoint (keeps full emails locally) |

## Flags / scoring

| Flag | Meaning |
|------|---------|
| `LOW_COMMITS` | Fewer than N commits (default &lt; 10) |
| `LOW_COMMITS_NONCODE` | Low commits, hardware/Figma/no-code (soft) |
| `ALL_COMMITS_OUTSIDE_WINDOW` | Every commit outside `[start, end]` |
| `PRE_START_COMMITS` / `ALL_COMMITS_BEFORE_START` / `MEANINGFUL_WORK_BEFORE_START` | Work before start |
| `PRE_START_DQ` | **Automatic mark** — any pre-start commit activity |
| `POST_END_COMMITS` / `POST_END_HEAVY` | Work after end (soft unless it dominates) |
| `BULK_DUMP` | Tiny history + large repo size |
| `REPO_UNAVAILABLE` | Private / 404 / blocked |
| `COMMIT_FETCH_INCOMPLETE` | GitHub commit window counts unreliable |
| `PROJECT_RELEVANCE` / `PROJECT_IN_RECENT_COMMITS` | Project/hackathon markers found (softens only if no early work) |
| `NO_PROJECT_RELEVANCE` | No markers in name, commits, or README |
| `NO_EMAIL_OVERLAP` | Only with `--checkin-csv` + real author emails that don’t match |
| `AUTHOR_EMAILS_HIDDEN` | Authors are GitHub noreply — overlap skipped |
| `TEAM_NOT_CHECKED_IN` / `PARTIAL_CHECKIN` | Matched portal emails not checked in |
| `CHECKIN_UNMATCHED` | Team emails missing from portal CSV (not a no-show DQ) |
| `GEMINI_SUSPICIOUS` / `GEMINI_OK` | Optional `--gemini` verdict |
| `LIKELY_NONCODE` | Hardware / Figma / no-code signals |

### Presets (`--preset`)

| Preset | Marked if score ≥ |
|--------|-------------------|
| `strict` | 15 |
| `review` (default) | 30 |
| `loose` | 50 |

## CLI reference

| Flag | Default |
|------|---------|
| `--projects-csv` | required (or `PROJECTS_CSV`) |
| `--hackathon` | recommended; auto-detect from submission URLs when possible |
| `--start` / `--end` / `--tz` | required window |
| `--scrape-devpost` / `--no-scrape-devpost` | on |
| `--scrape-users` / `--no-scrape-users` | on |
| `--workers` | 8 |
| `--checkin-csv` / `--checkin-column` | off |
| `--gemini` | off — needs `GEMINI_API_KEY` + `google-genai` |
| `--gemini-all` | off — scan every project (costlier) |
| `--gemini-model` | `gemini-2.5-flash` |
| `--include-pii` | off |
| `--resume` | off — refuses if checkpoint window/`min_score` differs |
| `--limit` | off — first N projects (smoke tests) |

### Optional Gemini

```bash
pip install google-genai   # already listed in requirements.txt
# GEMINI_API_KEY=... in .env

python -m vibe_check ... --gemini
python -m vibe_check ... --gemini --gemini-all
```

By default Gemini reviews **marked + near-misses**. Columns: `gemini_verdict`, `gemini_confidence`, `gemini_summary`. Gemini never clears `PRE_START_DQ`.

## Design notes

- **Team profiles** contribute GitHub usernames only; discovery attaches a repo only on strong name match (or hackathon-named repo when the project had zero links).
- **Large repos** use GitHub Link-header commit counts for pre/post window math; truncated fetches set `COMMIT_FETCH_INCOMPLETE`.
- **Check-in CSV** only DQs teams whose emails matched the portal index. Unmatched emails are informational.
- Prefer sharing `marked.csv` / `report.md` over `all_results.json` if you care about email redaction.

## Env (`.env.example`)

```bash
GITHUB_TOKEN=ghp_...
PROJECTS_CSV=path/to/projects-export.csv
HACKATHON_URL=https://your-event.devpost.com
HACKATHON_TZ=America/Los_Angeles
HACKATHON_START=2026-10-03T12:00:00
HACKATHON_END=2026-10-04T12:00:00
# CHECKIN_CSV=...
# GEMINI_API_KEY=...
# GEMINI_MODEL=gemini-2.5-flash
```
