from vibe_check.projects_csv import load_projects_csv
from vibe_check.scraper import extract_repos


def test_extract_repos_skips_junk():
    text = (
        "See https://github.com/alice/cool-app and "
        "https://github.com/torvalds/linux and "
        "https://github.com/features/actions"
    )
    repos = extract_repos(text)
    assert repos == ["alice/cool-app"]


def test_load_projects_csv_basic(tmp_csv):
    # Minimal Devpost-style header (normalize pads team columns).
    header = (
        "Project Title,Submission Url,About The Project,Built With,"
        "Opt-In Prizes,Try it out links,"
        "Team Member 1 First Name,Team Member 1 Last Name,Team Member 1 Email"
    )
    path = tmp_csv(
        "projects.csv",
        header
        + "\n"
        + "Cool App,https://example.devpost.com/submissions/cool-app,"
        + "A neat tool,Python,"
        + ",https://github.com/alice/cool-app,"
        + "Alice,Smith,Alice@School.EDU\n",
    )
    projects = load_projects_csv(path)
    assert len(projects) == 1
    p = projects[0]
    assert p.title == "Cool App"
    assert p.slug == "cool-app"
    assert p.github_repos == ["alice/cool-app"]
    assert p.team_emails == ["alice@school.edu"]
