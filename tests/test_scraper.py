from datetime import UTC, datetime
from unittest.mock import MagicMock

from vibe_check.config import RunConfig
from vibe_check.models import Project
from vibe_check.scraper import (
    DevpostScraper,
    _normalize_title,
    _valid_software_slug,
    extract_devpost_users,
    extract_github_users,
    extract_repos,
    guess_hackathon_url,
)


def test_extract_repos_keeps_real_links_skips_junk():
    html = """
    <a href="https://github.com/alice/cool-app">code</a>
    <a href="https://github.com/features/actions">noise</a>
    <a href="https://github.com/torvalds/linux">noise</a>
    Try https://github.com/bob/my-hack.git
    """
    repos = extract_repos(html)
    assert "alice/cool-app" in repos
    assert "bob/my-hack" in repos
    assert not any(r.lower().startswith("features/") for r in repos)
    assert not any("torvalds" in r.lower() for r in repos)


def test_extract_github_users_from_profile_and_repo_links():
    text = "https://github.com/alice and https://github.com/bob/repo"
    users = extract_github_users(text)
    assert "alice" in users
    assert "bob" in users


def test_extract_devpost_users_skips_site_paths():
    html = """
    <a href="https://devpost.com/alice">Alice</a>
    <a href="https://devpost.com/software/cool-app">project</a>
    <a href="https://devpost.com/hackathons">noise</a>
    """
    users = extract_devpost_users(html)
    assert "alice" in users
    assert "software" not in users
    assert "hackathons" not in users


def test_guess_hackathon_url_from_submission():
    projects = [
        Project(
            title="A",
            url="https://stormhacks2026.devpost.com/submissions/1-a",
            slug="1-a",
        )
    ]
    assert guess_hackathon_url(projects) == "https://stormhacks2026.devpost.com"


def test_valid_software_slug_rejects_submission_ids():
    assert _valid_software_slug("cool-app") is True
    assert _valid_software_slug("1213947-crimepath") is False


def test_parse_gallery_page_extracts_software_entries():
    cfg = RunConfig(
        hackathon_url="https://example.devpost.com",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 2, tzinfo=UTC),
    )
    scraper = DevpostScraper(MagicMock(), cfg)
    html = """
    <div class="gallery-item">
      <a href="/software/cool-app"><h5>Cool App</h5></a>
      <p>A neat demo</p>
    </div>
    <div class="software-entry">
      <a href="https://devpost.com/software/other-thing"><h3>Other Thing</h3></a>
    </div>
    """
    projects = scraper._parse_gallery_page(html)
    slugs = {p.slug for p in projects}
    assert "cool-app" in slugs
    assert "other-thing" in slugs
    cool = next(p for p in projects if p.slug == "cool-app")
    assert cool.title == "Cool App"
    assert cool.url.endswith("/software/cool-app")


def test_resolve_missing_urls_uses_title_map_and_slug_guess():
    cfg = RunConfig(
        hackathon_url="https://example.devpost.com",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 2, tzinfo=UTC),
    )
    scraper = DevpostScraper(MagicMock(), cfg)
    scraper.build_title_software_map = MagicMock(  # type: ignore[method-assign]
        return_value={_normalize_title("Cool App"): "https://devpost.com/software/cool-app"}
    )
    projects = [
        Project(title="Cool App", url="csv://missing", slug="1213968-cool-app"),
        Project(title="Orphan", url="", slug="orphan-slug"),
        Project(title="Has Url", url="https://devpost.com/software/has-url", slug="has-url"),
    ]
    fixed = scraper.resolve_missing_urls(projects)
    assert fixed == 2
    assert projects[0].url == "https://devpost.com/software/cool-app"
    assert projects[1].url == "https://devpost.com/software/orphan-slug"
    assert projects[2].url == "https://devpost.com/software/has-url"


def test_resolve_missing_urls_skips_without_hackathon():
    cfg = RunConfig(
        hackathon_url="csv-import",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 2, tzinfo=UTC),
    )
    scraper = DevpostScraper(MagicMock(), cfg)
    projects = [Project(title="Cool App", url="", slug="cool-app")]
    assert scraper.resolve_missing_urls(projects) == 0
    assert projects[0].url == ""
