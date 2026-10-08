from pathlib import Path

from vibe_check.attendees import load_attendee_csv
from vibe_check.projects_csv import load_projects_csv

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_fixture_projects_sample_loads():
    projects = load_projects_csv(FIXTURES / "projects-sample.csv")
    assert len(projects) == 3
    assert projects[0].slug == "cool-app"
    assert "alice@example.com" in projects[0].team_emails
    assert projects[0].github_repos == ["alice-demo/cool-app"]


def test_fixture_attendee_samples_load():
    luma = load_attendee_csv(FIXTURES / "luma-sample.csv", preset="luma")
    portal = load_attendee_csv(
        FIXTURES / "portal-sample.csv",
        preset="portal",
        checkin_column="StormHacks 2026 Check In",
    )
    assert luma["alice@example.com"].accepted is True
    assert portal["alice@example.com"].checked_in is True
    assert portal["bob@example.com"].checked_in is False
