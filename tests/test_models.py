from vibe_check.models import github_repo_url, normalize_repo_slug


def test_normalize_repo_slug_from_url():
    assert normalize_repo_slug("https://github.com/Acme/Foo.git") == "Acme/Foo"
    assert normalize_repo_slug("owner/repo") == "owner/repo"


def test_github_repo_url():
    assert github_repo_url("owner/repo") == "https://github.com/owner/repo"
    assert github_repo_url("https://github.com/owner/repo/") == "https://github.com/owner/repo"
    assert github_repo_url("") == ""
    assert github_repo_url("-") == ""
