from vibe_check.models import VibeResult
from vibe_check.review_site import write_review_site


def test_write_review_site_omits_emails_and_lists_flagged(tmp_path):
    marked = [
        VibeResult(
            suspicion_score=53,
            flags=["PRE_START_REVIEW", "PRE_START_COMMITS"],
            reasons=["1 commit before start"],
            project_title="Cubic",
            project_url="https://example.devpost.com/submissions/cubic",
            primary_repo="alice/cubic",
            bucket="marked",
            team_emails=["secret@school.edu"],
            author_emails=["dev@users.noreply.github.com"],
            pre_start_commits=1,
            total_commits=20,
            in_window_commits=19,
        )
    ]
    safe = [
        VibeResult(
            suspicion_score=5,
            flags=[],
            project_title="Safe App",
            project_url="https://example.devpost.com/submissions/safe",
            primary_repo="bob/safe",
            bucket="likely_safe",
            total_commits=40,
            in_window_commits=40,
        ),
        VibeResult(
            suspicion_score=5,
            flags=["NO_GITHUB"],
            reasons=["no public GitHub repo found"],
            project_title="Missing Link",
            project_url="https://example.devpost.com/submissions/missing",
            primary_repo="",
            bucket="likely_safe",
        ),
    ]
    out = write_review_site(
        tmp_path / "review" / "index.html",
        marked=marked,
        likely_safe=safe,
        summary={
            "projects_loaded": 3,
            "marked": 1,
            "likely_safe": 2,
            "no_github": 1,
        },
        meta={
            "hackathon_url": "https://example.devpost.com",
            "window_local": "start → end",
            "preset": "review",
            "min_score": 30,
        },
    )
    html = out.read_text(encoding="utf-8")
    assert "Cubic" in html
    assert "PRE_START_REVIEW" in html
    assert "Review queue, not an auto-DQ" in html
    assert "SFU Surge" in html
    assert "#6466f1" in html
    assert "secret@school.edu" not in html
    assert "alice/cubic" in html or "github.com/alice/cubic" in html
    assert "Can't find GitHub" in html
    assert "Missing Link" in html
    assert '"no_github"' in html
