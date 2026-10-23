from vibe_check.models import VibeResult
from vibe_check.review_site import hackathon_display_name, write_review_site


def test_hackathon_display_name():
    assert hackathon_display_name("https://stormhacks2026.devpost.com") == "StormHacks2026"
    assert hackathon_display_name("") == "Hackathon"


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
        VibeResult(
            suspicion_score=30,
            flags=["REPO_UNAVAILABLE"],
            reasons=["HTTP 404"],
            project_title="Private Repo",
            project_url="https://example.devpost.com/submissions/private",
            primary_repo="alice/secret",
            bucket="marked",
        ),
    ]
    out = write_review_site(
        tmp_path / "review" / "index.html",
        marked=marked,
        likely_safe=safe,
        summary={
            "projects_loaded": 4,
            # Flagged chip matches main tab (unavailable has its own tab).
            "marked": 1,
            "likely_safe": 1,
            "no_github": 1,
            "unavailable": 1,
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
    assert "secret@school.edu" not in html
    assert "Missing Link" in html
    assert '"no_github"' in html
    assert '"unavailable"' in html
    assert "Can't access repo" not in html  # renamed to "Unavailable"
    assert "Unavailable" in html
    assert "Private Repo" in html
    assert "Example Devpost Vibe Checker" in html
    assert "radial-gradient" not in html
    assert "↓ CSV" in html
    assert 'class="badge-warn"' in html
    assert "viewBox=" in html  # caution icon in banner
    csv_path = out.parent / "example-vibe-check.csv"
    assert csv_path.exists()
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "Cubic" in csv_text
    assert "secret@school.edu" not in csv_text
