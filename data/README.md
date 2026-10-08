# Local data (gitignored)

Put real organizer exports here (projects CSV, Luma, portal, registrants).
Nothing under `data/` is committed.

Example:

```bash
python -m vibe_check \
  --projects-csv data/projects-export.csv \
  --hackathon https://your-event.devpost.com \
  --start 2026-10-03T12:00:00 \
  --end 2026-10-04T12:00:00
```

For shareable samples without PII, see `examples/`.
