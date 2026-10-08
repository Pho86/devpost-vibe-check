# Anonymized sample CSVs

Fake `@example.com` rows for docs and smoke tests. **Do not** put real attendee/project exports here — use `data/` (gitignored).

```bash
python -m vibe_check \
  --projects-csv examples/projects-sample.csv \
  --hackathon https://example.devpost.com \
  --start 2026-10-03T12:00:00 \
  --end 2026-10-04T12:00:00 \
  --no-scrape-devpost \
  --no-scrape-users \
  --luma-csv examples/luma-sample.csv \
  --portal-csv examples/portal-sample.csv
```
