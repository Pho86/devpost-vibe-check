# Devpost Vibe Check

A local CLI that helps hackathon organizers triage Devpost submissions by looking at GitHub history against the event window. You get a ranked review queue instead of reading every repo by hand.

Point it at an organizer projects export (and optionally attendee lists). It follows each team to GitHub, scores commits against your start/end times, and writes flagged rows plus a likely-safe list.

Typical signals: very few commits, work before hacking starts, bulk dumps, repos that do not match the project name, and (opt-in) team emails missing from accepted or check-in CSVs. An optional Gemini pass can add a second opinion on flagged rows.

Works with any Devpost hackathon. Optional portal / Luma-style check-in when you have it, and fine as Devpost-only (projects CSV + window, no attendee files).

**This tool does not disqualify anyone.** `marked.csv` is a review queue. Every flag needs a human decision. High scores and tags like `PRE_START_REVIEW` mean "look at this first," not "auto-DQ."

## Modes

| Mode | What you pass | What you get |
|------|---------------|--------------|
| **Devpost-only** | `--projects-csv` + `--start` / `--end` | Git-history review queue |
| **+ attendees** | Also `--portal-csv` / `--luma-csv` / `--registrants-csv` / `--accepted-csv` | Same queue, plus acceptance/check-in flags only on already-flagged rows |

## Pipeline

```text
1. CSV          organizer export (title, submission URL, Try-it-out links, team emails)
2. Devpost      submission pages → GitHub links + team member profile URLs
3. Team users   Devpost profiles → GitHub usernames (not every portfolio repo)
4. Discovery    match user repos by project name / in-window push
5. Git history  window scoring + project/hackathon name in commits/README
6. Gemini       optional second-pass (--gemini)
```

## Project structure

```text
devpost-vibe-check/
├── pyproject.toml          # package meta, deps, extras, console scripts
├── requirements.txt        # core runtime (mirrors pyproject; no pandas/gemini)
├── requirements-dev.txt    # pinned pytest, ruff, pre-commit
├── .pre-commit-config.yaml # local ruff check + format on commit
├── README.md
├── .env.example / .env     # GITHUB_TOKEN, optional Gemini / Netlify keys
├── .gitignore
│
├── .github/workflows/
│   └── ci.yml              # lint job, then pytest on 3.11 + 3.12
│
├── vibe_check/             # main package (python -m vibe_check)
│   ├── __main__.py         # entry → cli.main()
│   ├── __init__.py         # version
│   ├── cli.py              # argparse only
│   ├── pipeline.py         # orchestration (load → scrape → score → report)
│   ├── config.py           # RunConfig, presets, thresholds
│   ├── models.py           # Project, RepoAnalysis, VibeResult
│   ├── projects_csv.py     # load organizer projects export
│   ├── scraper.py          # Devpost pages → GitHub links/users
│   ├── github_client.py    # commits, repos, history
│   ├── relevance.py        # name/README/hackathon matching
│   ├── scoring.py          # flags + suspicion scores
│   ├── attendees.py        # portal / Luma / registrants CSVs (opt-in)
│   ├── gemini_scan.py      # optional second-pass LLM review
│   ├── report.py           # CSV/JSON/MD outputs + console tables
│   ├── review_site.py      # static HTML review queue for co-reviewers
│   ├── publish.py          # --publish → Netlify unique share URL
│   └── http_util.py        # shared HTTP client
│
├── data/                   # real exports (gitignored except README/.gitkeep)
├── examples/               # sample CSVs for smoke runs
├── output/                 # marked / likely_safe / combined / report
│
└── tests/
    ├── conftest.py
    ├── fixtures/           # sample CSVs for tests
    ├── test_attendees.py
    ├── test_fixtures.py
    ├── test_models.py
    ├── test_projects_csv.py
    ├── test_relevance.py
    ├── test_review_site.py
    ├── test_publish.py
    └── test_scoring.py
```

Flow: `cli` builds config, `pipeline` loads projects, `scraper` + `github_client` gather data, `relevance` + `scoring` judge (+ optional attendees / Gemini), then `report` and the review site write `output/`.

## Quick start

```bash
cd devpost-vibe-check
python -m venv .venv
source .venv/Scripts/activate   # or: source .venv/bin/activate
pip install -e .                 # core CLI (also: vibe-check / devpost-vibe-check)
# pip install -e ".[dev]"        # + pytest, ruff, pre-commit
# pip install -e ".[gemini]"     # optional --gemini second pass
# pre-commit install             # ruff check/format on every commit
cp .env.example .env
# set GITHUB_TOKEN=...
# put real exports in data/ (gitignored); try examples/*.csv first
```

If `--start` / `--end` have no timezone, they are read in `--tz` (default `America/Los_Angeles`).

## Config precedence

1. CLI flag (if you pass it)
2. Env var / `.env` (argparse default when the flag is omitted)
3. Preset or code default (`--preset` sets `min_score`; other knobs live in `config.py`)

Example: `--min-score 40` beats `--preset review` (30). `PROJECTS_CSV` in `.env` is used only if you skip `--projects-csv`.

## Example commands

Base Devpost-only run (default `--preset review`):

```bash
python -m vibe_check \
  --projects-csv path/to/projects-export.csv \
  --hackathon https://your-event.devpost.com \
  --start 2026-10-03T12:00:00 \
  --end 2026-10-04T12:00:00 \
  --tz America/Los_Angeles
```

Same idea, only the flags that change:

```bash
# Smoke test
python -m vibe_check ... --limit 12 --workers 8

# Stricter / looser review threshold
python -m vibe_check ... --preset strict    # score >= 15
python -m vibe_check ... --preset loose     # score >= 50
python -m vibe_check ... --min-score 40     # custom cutoff

# Organizer portal export (+ optional check-in column)
python -m vibe_check ... \
  --portal-csv path/to/portal-export.csv \
  --checkin-column "Check In"

# Luma / registrants / auto-detect lists (repeatable)
python -m vibe_check ... --luma-csv path/to/luma-guests.csv
python -m vibe_check ... --registrants-csv path/to/devpost-registrants.csv
python -m vibe_check ... --accepted-csv path/to/any-list.csv --accepted-csv path/to/another.csv

# Optional Gemini second-pass (GEMINI_API_KEY in .env)
python -m vibe_check ... --gemini
python -m vibe_check ... --gemini --gemini-all
python -m vibe_check ... --gemini --gemini-model gemini-2.5-flash

# Resume (same --start/--end/--min-score as the checkpoint, or it refuses)
python -m vibe_check ... --resume

# Use only GitHub links already in the CSV
python -m vibe_check ... --no-scrape-devpost --no-scrape-users

# Publish the review site (NETLIFY_AUTH_TOKEN in .env)
python -m vibe_check ... --publish

# Public gallery instead of organizer CSV
python -m vibe_check \
  --from-gallery \
  --hackathon https://your-event.devpost.com \
  --start 2026-10-03T12:00:00 \
  --end 2026-10-04T12:00:00 \
  --max-pages 5

# After filling .env (PROJECTS_CSV, HACKATHON_*, GITHUB_TOKEN, ...)
python -m vibe_check --preset review --workers 8
```

## Defaults that matter

- Organizer CSV first (public galleries often close early)
- Devpost + team-profile scrape on by default
- One row per project (multi-repo teams collapse to the worst candidate repo by name/relevance, not random portfolio repos)
- Pre-start commits get `PRE_START_REVIEW` in the queue. Still not an auto-DQ
- Attendee / check-in joins are opt-in. Those flags only land on projects already flagged by git heuristics
- `output/` is cleared at the start of each run unless you pass `--resume`

## Outputs (`output/`)

| File | Contents |
|------|----------|
| `marked.csv` / `.json` | Flagged for review (score >= preset). Not a DQ list |
| `likely_safe.csv` / `.json` | Below threshold |
| `combined.csv` / `.json` | Everyone |
| `report.md` | Summary tables |
| `review/index.html` | Shareable review site (flagged first, filterable, no team emails) |
| `all_results.json` | Resume checkpoint (keeps full emails locally) |

Open `output/review/index.html` locally, or pass `--publish` to upload it:

```bash
# NETLIFY_AUTH_TOKEN in .env
python -m vibe_check ... --publish
# → https://vibe-<code>.netlify.app
# code also saved in output/review/share.txt
```

Set `VIBE_PUBLISH=1` to publish every run. Optional `NETLIFY_SITE_ID` reuses one Netlify site (you still get a unique deploy URL). Keep PII in the local CSVs; the HTML leaves team emails out.

Sample inputs (fake `@example.com` data) live in `examples/`. Put real exports in `data/` (gitignored).

### How to read `marked.csv`

Sort is most-suspicious first. Open the project URL and `primary_repo`, then sanity-check the flags. None of these are automatic disqualifications.

| You see | What to verify |
|---------|----------------|
| `PRE_START_REVIEW` / `PRE_START_COMMITS` | Were commits really before start, or bad timezone / wrong repo / reused homework? |
| `LOW_COMMITS` / `BULK_DUMP` | Tiny history + large tree can mean a zip dump. Hardware/Figma may be fine (`LIKELY_NONCODE`) |
| `ALL_COMMITS_OUTSIDE_WINDOW` | Wrong repo linked, or work entirely outside the event? |
| `NO_PROJECT_RELEVANCE` | Repo name/README do not match the Devpost title. Confirm the link |
| `REPO_UNAVAILABLE` | Private or 404. Ask the team for access. Do not treat as guilt |
| `TEAM_NOT_ACCEPTED` / `TEAM_NOT_CHECKED_IN` | Only on already-flagged rows. Check CSV columns and email spelling |
| `GEMINI_SUSPICIOUS` | Second opinion only. Read `gemini_summary`, then decide yourself |

Useful columns: `suspicion_score`, `flags`, `reasons`, `primary_repo`, `pre_start_commits`, `in_window_commits`, `sample_messages`.

## Flags / scoring

| Flag | Meaning |
|------|---------|
| `LOW_COMMITS` | Fewer than N commits (default &lt; 10) |
| `LOW_COMMITS_NONCODE` | Low commits, hardware/Figma/no-code (soft) |
| `ALL_COMMITS_OUTSIDE_WINDOW` | Every commit outside `[start, end]` |
| `PRE_START_COMMITS` / `ALL_COMMITS_BEFORE_START` / `MEANINGFUL_WORK_BEFORE_START` | Work before start |
| `PRE_START_REVIEW` | Priority review flag for pre-start activity (not an auto-DQ) |
| `POST_END_COMMITS` / `POST_END_HEAVY` | Work after end (soft unless it dominates) |
| `BULK_DUMP` | Tiny history + large repo size |
| `REPO_UNAVAILABLE` | Private / 404 / blocked |
| `COMMIT_FETCH_INCOMPLETE` | GitHub commit window counts unreliable |
| `PROJECT_RELEVANCE` / `PROJECT_IN_RECENT_COMMITS` | Project/hackathon markers found (softens only if no early work) |
| `NO_PROJECT_RELEVANCE` | No markers in name, commits, or README |
| `NO_EMAIL_OVERLAP` | Attendee CSV on + real author emails that do not match team |
| `AUTHOR_EMAILS_HIDDEN` | Authors use GitHub noreply; overlap skipped |
| `TEAM_NOT_ACCEPTED` / `PARTIAL_ACCEPTED` | Team emails not on Luma/portal/registrants accepted list |
| `ACCEPTED_UNMATCHED` | Team emails missing from attendee CSVs (informational) |
| `TEAM_NOT_CHECKED_IN` / `PARTIAL_CHECKIN` | Matched emails not checked in day-of |
| `CHECKIN_UNMATCHED` | Team emails missing from check-in data (informational) |
| `GEMINI_SUSPICIOUS` / `GEMINI_OK` | Optional `--gemini` verdict |
| `LIKELY_NONCODE` | Hardware / Figma / no-code signals |

### Presets (`--preset`)

| Preset | Flagged for review if score >= |
|--------|--------------------------------|
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
| `--portal-csv` / `--checkin-csv` | off (hacker portal, `Current Status=Accepted`) |
| `--luma-csv` | off (Luma guest export) |
| `--registrants-csv` | off (Devpost registrants; listed => accepted) |
| `--accepted-csv` | off (repeatable; auto-detect format) |
| `--checkin-column` | portal check-in column name |
| `--gemini` | off (needs `GEMINI_API_KEY` + `pip install -e ".[gemini]"`) |
| `--gemini-all` | off (scan every project; costlier) |
| `--gemini-model` | `gemini-2.5-flash` |
| `--publish` | off (upload `review/` to Netlify; `NETLIFY_AUTH_TOKEN` or `VIBE_PUBLISH=1`) |
| `--resume` | off (refuses if hackathon URL, window, scrape flags, or thresholds differ) |
| `--limit` | off (first N projects for smoke tests) |

Gemini: set `GEMINI_API_KEY`, then `--gemini`. By default it reviews flagged rows plus near-misses. `--gemini-all` scans everyone. It never clears `PRE_START_REVIEW`.

## Known limitations

Flags can lie. Treat each one as a prompt to look, not a verdict.

- Private or 404 repos: no history to score (`REPO_UNAVAILABLE`). Ask the team for access.
- Force-pushed or rewritten history: GitHub only sees what is there now.
- Rebase / amend: author date and committer date can differ. Scoring uses the earlier of the two (`min`), so a rebase that keeps old author dates still counts as pre-start work.
- Squashed histories: one large commit can look like a bulk dump or hide earlier work.
- Wrong repo linked: portfolio or homework repos can look pre-built. Relevance checks help but are heuristic.
- Timezone mistakes: naive `--start`/`--end` follow `--tz`. Wrong TZ shifts the whole window.
- Hidden author emails: GitHub noreply skips email-overlap checks (`AUTHOR_EMAILS_HIDDEN`).

## Operational notes

- **GitHub token:** set `GITHUB_TOKEN`. Without it, unauthenticated limits are tiny and runs stall. The client backs off on HTTP 403/429 and spaces requests (`GITHUB_DELAY_S`).
- **Workers:** `--workers 8` is a solid default. Drop to 2-4 if you get throttled hard.
- **Gemini:** optional. Free-tier quotas hit 429 easily. Prefer the default (flagged + near-misses) over `--gemini-all`.
- **Progress:** Devpost scrape, GitHub analyze, and scoring show Rich bars with ETA.
- **Resume:** if a long run dies, reuse the same hackathon URL, window, scrape flags, and thresholds with `--resume`.
- **Install freeze:** `requirements.txt` uses minimum versions. For an event freeze: `pip freeze > requirements.lock` (optional, not checked in).

## Design notes

- Team profiles contribute GitHub usernames only. Discovery attaches a repo on a strong name match (or one hackathon-named repo when the project had zero links).
- Large repos use GitHub Link-header commit counts for pre/post window math. Truncated fetches set `COMMIT_FETCH_INCOMPLETE`.
- Attendee joins run only when you pass those CSV flags. Acceptance/check-in flags apply only if the project is already marked by git heuristics.
- Day-of check-in uses a check-in column when present on an opt-in portal/check-in CSV.
- Keep real CSVs under `data/` (ignored). Share `examples/` only.

## Env (`.env.example`)

```bash
GITHUB_TOKEN=ghp_...
PROJECTS_CSV=path/to/projects-export.csv
HACKATHON_URL=https://your-event.devpost.com
HACKATHON_TZ=America/Los_Angeles
HACKATHON_START=2026-10-03T12:00:00
HACKATHON_END=2026-10-04T12:00:00
# PORTAL_CSV=...
# LUMA_CSV=...
# REGISTRANTS_CSV=...
# CHECKIN_CSV=...
# GEMINI_API_KEY=...
# GEMINI_MODEL=gemini-2.5-flash
# NETLIFY_AUTH_TOKEN=...
# NETLIFY_SITE_ID=...
# VIBE_PUBLISH=1
```
