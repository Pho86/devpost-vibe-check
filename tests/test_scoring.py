from vibe_check.config import RunConfig
from vibe_check.models import Project, RepoAnalysis
from vibe_check.scoring import looks_noncode, score_project


def _project(**kwargs) -> Project:
    base = dict(
        title="Cool App",
        url="https://example.devpost.com/submissions/cool-app",
        slug="cool-app",
        team_emails=["alice@school.edu"],
    )
    base.update(kwargs)
    return Project(**base)


def test_looks_noncode_hardware():
    p = _project(built_with="Arduino, sensors", github_repos=[])
    assert looks_noncode(p) is True


def test_looks_noncode_software():
    p = _project(built_with="React, FastAPI", github_repos=["alice/cool-app"])
    assert looks_noncode(p) is False


def test_pre_start_commits_auto_mark(run_config: RunConfig):
    analysis = RepoAnalysis(
        repo="alice/cool-app",
        total_commits=20,
        pre_start_commits=3,
        in_window_commits=17,
        earliest_commit_at="2026-10-01T12:00:00+00:00",
        name_match_score=1.0,
        relevance_sources=["repo_name"],
        relevance_hits=["cool-app"],
    )
    result = score_project(_project(), [analysis], run_config)
    assert "PRE_START_COMMITS" in result.flags
    assert "PRE_START_DQ" in result.flags
    assert result.suspicion_score >= run_config.min_score


def test_healthy_in_window_repo_not_marked(run_config: RunConfig):
    analysis = RepoAnalysis(
        repo="alice/cool-app",
        total_commits=25,
        in_window_commits=25,
        name_match_score=1.0,
        relevance_sources=["repo_name", "commits"],
        relevance_hits=["cool-app"],
    )
    result = score_project(_project(), [analysis], run_config)
    assert "PRE_START_DQ" not in result.flags
    assert result.suspicion_score < run_config.min_score


def test_attendee_flags_only_when_already_marked(run_config: RunConfig):
    # Low-commit software project → marked by heuristics
    marked_analysis = RepoAnalysis(
        repo="alice/thin",
        total_commits=2,
        in_window_commits=2,
        name_match_score=1.0,
    )
    p = _project(
        title="Thin",
        slug="thin",
        github_repos=["alice/thin"],
        accepted_members=0,
        not_accepted_emails=["alice@school.edu"],
        team_emails=["alice@school.edu"],
    )
    marked = score_project(p, [marked_analysis], run_config)
    assert marked.suspicion_score >= run_config.min_score
    assert "TEAM_NOT_ACCEPTED" in marked.flags

    # Healthy repo stays below threshold — attendee mismatch must not mark it
    healthy = RepoAnalysis(
        repo="alice/cool-app",
        total_commits=30,
        in_window_commits=30,
        name_match_score=1.0,
        relevance_sources=["repo_name", "commits"],
        relevance_hits=["cool-app"],
    )
    p2 = _project(
        accepted_members=0,
        not_accepted_emails=["alice@school.edu"],
    )
    safe = score_project(p2, [healthy], run_config)
    assert safe.suspicion_score < run_config.min_score
    assert "TEAM_NOT_ACCEPTED" not in safe.flags


def test_score_no_repo(run_config: RunConfig):
    result = score_project(_project(github_repos=[]), [], run_config)
    assert "NO_GITHUB" in result.flags
