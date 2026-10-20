from datetime import UTC, datetime, timedelta

from vibe_check.config import RunConfig
from vibe_check.github_client import GitHubClient
from vibe_check.models import RepoAnalysis


def _cfg() -> RunConfig:
    start = datetime(2026, 10, 3, 19, 0, tzinfo=UTC)
    end = datetime(2026, 10, 4, 19, 0, tzinfo=UTC)
    return RunConfig(
        hackathon_url="https://example.devpost.com",
        start=start,
        end=end,
        min_score=30,
    )


def test_apply_window_stats_uses_newest_sample_messages():
    client = GitHubClient.__new__(GitHubClient)
    client.config = _cfg()
    out = RepoAnalysis(repo="a/b")
    start = client.config.start
    dated = [
        (start - timedelta(hours=2), "old work", "a@x.com"),
        (start + timedelta(hours=1), "hackathon commit", "b@x.com"),
        (start + timedelta(hours=3), "latest polish", "c@x.com"),
    ]
    client._apply_window_stats(out, dated)
    assert out.earliest_commit_at.startswith("2026-10-03")
    assert out.in_window_commits == 2
    assert out.pre_start_commits == 1
    # Newest messages first for relevance matching.
    assert out.sample_messages.startswith("latest polish")


def test_refine_large_repo_sets_earliest_from_oldest_page():
    cfg = _cfg()
    client = GitHubClient.__new__(GitHubClient)
    client.config = cfg
    out = RepoAnalysis(repo="a/big", default_branch="main", total_commits=1000)
    # Recent-only sample would wrongly look fully in-window.
    sample = [
        (cfg.start + timedelta(hours=2), "recent", "a@x.com"),
        (cfg.start + timedelta(hours=4), "newer", "a@x.com"),
    ]
    client._apply_window_stats(out, sample)

    oldest_dt = cfg.start - timedelta(days=30)

    def fake_count(repo, sha=None, *, until=None, since=None):
        if until == cfg.start:
            return 40
        if since == cfg.end:
            return 0
        if until == cfg.early_cutoff:
            return 45
        return 1000

    def fake_oldest(repo, *, sha=None, max_commits=50):
        return [
            {
                "commit": {
                    "author": {
                        "date": oldest_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "email": "og@x.com",
                    },
                    "committer": {
                        "date": oldest_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "email": "og@x.com",
                    },
                    "message": "real first feature",
                }
            }
        ]

    client.count_commits = fake_count  # type: ignore[method-assign]
    client._list_oldest_commits = fake_oldest  # type: ignore[method-assign]

    client._refine_large_repo_window(out, "a/big", total=1000, sample_dated=sample)
    assert out.pre_start_commits == 40
    assert out.post_end_commits == 0
    assert out.in_window_commits == 960
    assert out.early_window_commits == 45
    assert out.first_meaningful_commit_at.startswith("2026-09")
    assert out.all_commits_before_start is False
