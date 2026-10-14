# Devpost Vibe Check

Hackathon organizers often need a fast way to spot submissions that look unfinished, prebuilt, or inconsistent with the event window — without reading every GitHub repo by hand.

**Devpost Vibe Check** is a local CLI for that. You point it at a Devpost **organizer projects export** (and optionally attendee lists). It follows each team to GitHub, scores commit history against your hackathon start/end times, and writes a **flagged review queue** plus a likely-safe list.

Typical signals: very few commits, work before hacking starts, bulk dumps, repos that don’t match the project name, and (opt-in) team emails missing from accepted/check-in CSVs. An optional Gemini pass can add a second opinion on flagged rows.

Works with **any hackathon hosted on Devpost**. Built around SFU Surge portal / Luma-style check-in when you have it, and fully usable as **Devpost-only** (projects export + event window — no attendee CSVs required).

**This tool does not disqualify anyone.** `marked.csv` is a **review queue** for organizers — every flag needs a human decision. High scores and tags like `PRE_START_REVIEW` mean “look at this first,” not “auto-DQ.”

## Modes

| Mode | What you pass | What you get |
|------|---------------|--------------|
| **Devpost-only** | `--projects-csv` + `--start` / `--end` | Git-history review queue |
| **+ attendees** | Add `--portal-csv` / `--luma-csv` / `--registrants-csv` / `--accepted-csv` | Same queue, plus acceptance/check-in flags **only on already-flagged** rows |

## Pipeline

```text
1. CSV          organizer export (title, submission URL, Try-it-out links, team emails)
2. Devpost      submission pages → GitHub links + team member profile URLs
3. Team users   Devpost profiles → GitHub *usernames* (not every portfolio repo)
4. Discovery    match user repos by project name / in-window push
5. Git history  window scoring + project/hackathon name in commits/README
6. Gemini       optional second-pass (`--gemini`)
```

## Project structure

```text
devpost-vibe-check/
├── pyproject.toml          # package meta, pytest + ruff config
├── requirements.txt        # runtime deps
├── requirements-dev.txt    # pytest, ruff
├── README.md
├── .env.example / .env     # GITHUB_TOKEN, optional Gemini keys
├── .gitignore
│
├── .github/workflows/
│   └── ci.yml
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
    └── test_scoring.py
```

`cli` → `pipeline` loads projects → `scraper` + `github_client` gather → `relevance` + `scoring` (+ optional `attendees` / `gemini_scan`) → `report` writes `output/`.

## Quick start

```bash
cd devpost-vibe-check
python -m venv .venv
source .venv/Scripts/activate   # or: source .venv/bin/activate
pip install -r requirements.txt          # runtime (includes pandas)
# pip install -r requirements-dev.txt    # + pytest, ruff
cp .env.example .env
# set GITHUB_TOKEN=...
# put real exports in data/ (gitignored); try examples/*.csv first
```

Naive `--start` / `--end` are interpreted in `--tz` (default `America/Los_Angeles`).

## Config precedence

1. **CLI flag** (if passed)
2. **Env var** / `.env` (used as the argparse default when the flag is omitted)
3. **Preset / code default** (`--preset` → `min_score`; other knobs in `config.py`)

Example: `--min-score 40` overrides `--preset review` (30). `PROJECTS_CSV` in `.env` is used only if you omit `--projects-csv`.

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

Same command, only the changed flags:

```bash
# Smoke test
python -m vibe_check ... --limit 12 --workers 8

# Stricter / looser review threshold
python -m vibe_check ... --preset strict    # score ≥ 15
python -m vibe_check ... --preset loose     # score ≥ 50
python -m vibe_check ... --min-score 40     # custom cutoff

# SFU Surge portal (+ optional check-in column)
python -m vibe_check ... \
  --portal-csv path/to/portal-export.csv \
  --checkin-column "StormHacks 2026 Check In"

# Luma / registrants / auto-detect lists (repeatable)
python -m vibe_check ... --luma-csv path/to/luma-guests.csv
python -m vibe_check ... --registrants-csv path/to/devpost-registrants.csv
python -m vibe_check ... --accepted-csv path/to/any-list.csv --accepted-csv path/to/another.csv

# Optional Gemini second-pass (GEMINI_API_KEY in .env)
python -m vibe_check ... --gemini
python -m vibe_check ... --gemini --gemini-all
python -m vibe_check ... --gemini --gemini-model gemini-2.5-flash

# Resume (same --start/--end/--min-score as checkpoint, or it refuses)
python -m vibe_check ... --resume

# Use only GitHub links already in the CSV
python -m vibe_check ... --no-scrape-devpost --no-scrape-users

# Public gallery instead of organizer CSV
python -m vibe_check \
  --from-gallery \
  --hackathon https://your-event.devpost.com \
  --start 2026-10-03T12:00:00 \
  --end 2026-10-04T12:00:00 \
  --max-pages 5

# After filling .env (PROJECTS_CSV, HACKATHON_*, GITHUB_TOKEN, …)
python -m vibe_check --preset review --workers 8
```

## Defaults that matter

- **Organizer CSV first** (galleries are often closed early)
- **Devpost + user scrape on** by default
- **One row per project** (multi-repo teams collapsed; worst *candidate* repo wins — name/relevance matched, not teammate portfolio junk)
- **Pre-start commits = high-priority flag** (`PRE_START_REVIEW`) for the review queue — still not an auto-DQ
- **Attendee / check-in joins are opt-in** — only when you pass `--luma-csv` / `--portal-csv` / `--registrants-csv` / `--accepted-csv` / `--checkin-csv`, and acceptance/check-in **flags only apply to already-flagged** projects
- **`output/` cleared** at the start of each run (unless `--resume`)

## Outputs (`output/`)

| File | Contents |
|------|----------|
| `marked.csv` / `.json` | Flagged for review (score ≥ preset) — not a DQ list |
| `likely_safe.csv` / `.json` | Below threshold |
| `combined.csv` / `.json` | Everyone |
| `report.md` | Summary tables |
| `all_results.json` | Resume checkpoint (keeps full emails locally) |

Sample inputs (fake `@example.com` data): `examples/`. Put real organizer exports in `data/` (gitignored).

### How to read `marked.csv`

Sort is most-suspicious first. Open the **project URL** and **primary_repo**, then sanity-check the flags — none of these are automatic disqualifications.

| You see… | What to verify |
|----------|----------------|
| `PRE_START_REVIEW` / `PRE_START_COMMITS` | Were commits really before start, or timezone / wrong repo / reused homework? |
| `LOW_COMMITS` / `BULK_DUMP` | Tiny history + large tree → possible zip dump; hardware/Figma may be fine (`LIKELY_NONCODE`) |
| `ALL_COMMITS_OUTSIDE_WINDOW` | Wrong repo linked, or work entirely outside the event? |
| `NO_PROJECT_RELEVANCE` | Repo name/README don’t match the Devpost title — confirm it’s the submission |
| `REPO_UNAVAILABLE` | Private/404 — ask the team for access; don’t treat as guilt |
| `TEAM_NOT_ACCEPTED` / `TEAM_NOT_CHECKED_IN` | Only on already-flagged rows; confirm CSV columns + email spelling |
| `GEMINI_SUSPICIOUS` | Second opinion only — read `gemini_summary`, still decide yourself |

Useful columns: `suspicion_score`, `flags`, `reasons`, `primary_repo`, `pre_start_commits`, `in_window_commits`, `sample_messages`.

## Flags / scoring

| Flag | Meaning |
|------|---------|
| `LOW_COMMITS` | Fewer than N commits (default &lt; 10) |
| `LOW_COMMITS_NONCODE` | Low commits, hardware/Figma/no-code (soft) |
| `ALL_COMMITS_OUTSIDE_WINDOW` | Every commit outside `[start, end]` |
| `PRE_START_COMMITS` / `ALL_COMMITS_BEFORE_START` / `MEANINGFUL_WORK_BEFORE_START` | Work before start |
| `PRE_START_REVIEW` | Priority review flag — any pre-start commit activity (not an auto-DQ) |
| `POST_END_COMMITS` / `POST_END_HEAVY` | Work after end (soft unless it dominates) |
| `BULK_DUMP` | Tiny history + large repo size |
| `REPO_UNAVAILABLE` | Private / 404 / blocked |
| `COMMIT_FETCH_INCOMPLETE` | GitHub commit window counts unreliable |
| `PROJECT_RELEVANCE` / `PROJECT_IN_RECENT_COMMITS` | Project/hackathon markers found (softens only if no early work) |
| `NO_PROJECT_RELEVANCE` | No markers in name, commits, or README |
| `NO_EMAIL_OVERLAP` | Attendee CSV on + real author emails that don’t match team |
| `AUTHOR_EMAILS_HIDDEN` | Authors are GitHub noreply — overlap skipped |
| `TEAM_NOT_ACCEPTED` / `PARTIAL_ACCEPTED` | Team emails not on Luma/portal/registrants accepted list |
| `ACCEPTED_UNMATCHED` | Team emails missing from attendee CSVs (informational) |
| `TEAM_NOT_CHECKED_IN` / `PARTIAL_CHECKIN` | Matched emails not checked in day-of |
| `CHECKIN_UNMATCHED` | Team emails missing from check-in data (informational) |
| `GEMINI_SUSPICIOUS` / `GEMINI_OK` | Optional `--gemini` verdict |
| `LIKELY_NONCODE` | Hardware / Figma / no-code signals |

### Presets (`--preset`)

| Preset | Flagged for review if score ≥ |
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
| `--portal-csv` / `--checkin-csv` | off — hacker portal (`Current Status=Accepted`) |
| `--luma-csv` | off — Luma guest export (`Approval status`) |
| `--registrants-csv` | off — Devpost registrants (listed ⇒ accepted) |
| `--accepted-csv` | off — repeatable; auto-detect format |
| `--checkin-column` | portal check-in column name |
| `--gemini` | off — needs `GEMINI_API_KEY` + `google-genai` |
| `--gemini-all` | off — scan every project (costlier) |
| `--gemini-model` | `gemini-2.5-flash` |
| `--resume` | off — refuses if checkpoint window/`min_score` differs |
| `--limit` | off — first N projects (smoke tests) |

Gemini (optional): set `GEMINI_API_KEY`, then `--gemini`. Reviews **flagged + near-misses** by default; use `--gemini-all` for everyone. Never clears `PRE_START_REVIEW` (still a flag, not a verdict).

## Known limitations

These can produce misleading signals — treat every flag as a prompt to look, not a verdict:

- **Private / 404 repos** — no history to score (`REPO_UNAVAILABLE`); ask the team for access.
- **Force-pushed or rewritten history** — GitHub only sees the current history.
- **Rebasing / amend** — author date and committer date can diverge. Scoring uses the **earlier of author date and committer date** (`min`), so a rebase that preserves old author dates still counts as pre-start work.
- **Squashed histories** — a single large commit can look like a bulk dump or hide earlier work.
- **Wrong repo linked** — portfolio or homework repos can look “pre-built”; relevance checks help but are heuristic.
- **Timezone mistakes** — naive `--start`/`--end` follow `--tz`; wrong TZ shifts the window.
- **Hidden author emails** — GitHub noreply addresses skip email-overlap checks (`AUTHOR_EMAILS_HIDDEN`).

## Operational notes

- **GitHub token** — set `GITHUB_TOKEN`. Without it, unauthenticated rate limits are very low and runs will stall. The client backs off on HTTP 403/429 (`Retry-After` / secondary limits) and spaces requests (`GITHUB_DELAY_S`).
- **Workers** — `--workers 8` is a good default; drop to `2–4` if you see heavy throttling.
- **Gemini** — optional; free-tier quotas hit 429 easily. The scanner sleeps between calls; prefer default (flagged + near-misses) over `--gemini-all`.
- **Progress / ETA** — Devpost scrape, GitHub analyze, and scoring show Rich progress bars with elapsed + remaining estimates.
- **Resume** — if a long run dies mid-way: same `--start`/`--end`/`--min-score` plus `--resume`.
- **Reproducible installs** — `requirements.txt` uses minimum versions. For a freeze of what you ran at an event: `pip freeze > requirements.lock` (optional; not checked in).

## Design notes

- **Team profiles** contribute GitHub usernames only; discovery attaches a repo only on strong name match (or hackathon-named repo when the project had zero links).
- **Large repos** use GitHub Link-header commit counts for pre/post window math; truncated fetches set `COMMIT_FETCH_INCOMPLETE`.
- **Accepted attendees (opt-in)** — pass `--luma-csv` / `--portal-csv` / `--registrants-csv` / `--accepted-csv`. Joins run only when those flags are set. `TEAM_NOT_ACCEPTED` / check-in flags are applied **only if the project is already marked** by git heuristics.
- **Check-in** (day-of) uses a check-in column when present on an opt-in portal/check-in CSV.
- **Local PII** — keep real CSVs under `data/` (ignored). Share `examples/` only.

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
```
