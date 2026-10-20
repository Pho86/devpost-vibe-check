from dataclasses import replace

from vibe_check.config import RunConfig
from vibe_check.pipeline import _fingerprint_mismatch


def test_fingerprint_matches_identical_run(run_config: RunConfig):
    meta = {
        "fingerprint": run_config.resume_fingerprint(),
        **run_config.resume_fingerprint(),
    }
    assert _fingerprint_mismatch(meta, run_config) == []


def test_fingerprint_rejects_hackathon_and_scrape_changes(run_config: RunConfig):
    meta = {"fingerprint": run_config.resume_fingerprint()}
    other = replace(
        run_config,
        hackathon_url="https://other.devpost.com",
        scrape_users=False,
        min_score=50,
    )
    mismatches = _fingerprint_mismatch(meta, other)
    keys = {m.split(":")[0] for m in mismatches}
    assert "hackathon_url" in keys
    assert "scrape_users" in keys
    assert "min_score" in keys


def test_legacy_checkpoint_still_checks_window(run_config: RunConfig):
    # Pre-fingerprint checkpoints only stored start/end/min_score (+ hackathon).
    meta = {
        "hackathon_url": run_config.hackathon_url,
        "start": run_config.start.isoformat(),
        "end": run_config.end.isoformat(),
        "min_score": run_config.min_score,
    }
    assert _fingerprint_mismatch(meta, run_config) == []

    bad = replace(run_config, min_score=99)
    mismatches = _fingerprint_mismatch(meta, bad)
    assert any(m.startswith("min_score:") for m in mismatches)
