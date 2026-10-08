from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from vibe_check.config import RunConfig


@pytest.fixture
def tmp_csv(tmp_path: Path):
    def _write(name: str, text: str) -> Path:
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        return p

    return _write


@pytest.fixture
def run_config() -> RunConfig:
    start = datetime(2026, 10, 3, 19, 0, tzinfo=UTC)
    end = datetime(2026, 10, 4, 19, 0, tzinfo=UTC)
    return RunConfig(
        hackathon_url="https://example.devpost.com",
        start=start,
        end=end,
        min_score=30,
        preset="review",
    )
