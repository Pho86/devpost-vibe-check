from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

LOW_COMMIT_THRESHOLD = 10
EARLY_WINDOW_HOURS = 2

PRESETS = {
    "review": 30,
    "strict": 15,
    "loose": 50,
}

REQUEST_TIMEOUT = 30
DEVPOST_DELAY_S = 0.05
GITHUB_DELAY_S = 0.05
DEFAULT_WORKERS = 8
CHECKPOINT_EVERY = 25

BULK_DUMP_SIZE_KB = 400
BULK_DUMP_MAX_COMMITS = 2

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PACKAGE_ROOT / "output"
DEFAULT_TZ = "America/Los_Angeles"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Owners that appear in Devpost HTML / junk Try-it-out links — never treat as project repos.
SKIP_GITHUB_OWNERS = {
    "devpost",
    "features",
    "orgs",
    "topics",
    "sponsors",
    "marketplace",
    "newrelic",
    "settings",
    "github",
    "about",
    "apps",
    "enterprise",
    "pricing",
    "security",
    "customer-stories",
    "readme",
    "torvalds",  # e.g. accidental linux link
    "microsoft",
    "facebook",
    "google",
    "apple",
    "vercel",
    "npm",
    "nodejs",
}

SKIP_GITHUB_REPOS = {
    "issues",
    "pulls",
    "actions",
    "settings",
    "wiki",
    "projects",
    "pulse",
    "graphs",
    "network",
    "stargazers",
    "watchers",
}

NONCODE_HINTS = (
    "figma.com",
    "figma",
    "hardware",
    "arduino",
    "raspberry",
    "pcb",
    "wearable",
    "no-code",
    "nocode",
    "bubble.io",
    "webflow",
    "framer",
    "canva",
    "prototype only",
)


@dataclass(frozen=True)
class RunConfig:
    hackathon_url: str
    start: datetime
    end: datetime
    low_commit_threshold: int = LOW_COMMIT_THRESHOLD
    early_window_hours: int = EARLY_WINDOW_HOURS
    limit: int | None = None
    max_pages: int | None = None
    output_dir: Path = DEFAULT_OUTPUT_DIR
    resume: bool = False
    min_score: int = PRESETS["review"]
    preset: str = "review"
    projects_csv: Path | None = None
    scrape_devpost: bool = True
    scrape_users: bool = True
    workers: int = DEFAULT_WORKERS
    checkin_csv: Path | None = None
    checkin_column: str = "StormHacks 2026 Check In"
    # Extra attendee sources (Luma / portal / Devpost registrants / generic).
    accepted_csvs: tuple[Path, ...] = ()
    luma_csv: Path | None = None
    portal_csv: Path | None = None
    registrants_csv: Path | None = None
    display_tz: str = DEFAULT_TZ
    gemini: bool = False
    gemini_model: str = DEFAULT_GEMINI_MODEL
    gemini_all: bool = False  # False = marked + near-misses only

    # Back-compat alias used by older call sites / checkpoints.
    @property
    def scrape_missing(self) -> bool:
        return self.scrape_devpost

    @property
    def gallery_url(self) -> str:
        return f"{self.hackathon_url.rstrip('/')}/project-gallery"

    @property
    def early_cutoff(self) -> datetime:
        return self.start + timedelta(hours=self.early_window_hours)

    @property
    def has_attendee_csvs(self) -> bool:
        return bool(
            self.checkin_csv
            or self.luma_csv
            or self.portal_csv
            or self.registrants_csv
            or self.accepted_csvs
        )

    @property
    def email_overlap_enabled(self) -> bool:
        """Git↔Devpost email overlap when any attendee/check-in CSV is provided."""
        return self.has_attendee_csvs
