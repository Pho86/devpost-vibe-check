from vibe_check.models import Project
from vibe_check.relevance import (
    find_mentions,
    hackathon_tokens,
    name_similarity,
    project_tokens,
    relevance_needles,
)


def _project(**kwargs) -> Project:
    base = dict(
        title="Storm Pucks",
        url="https://stormhacks2026.devpost.com/submissions/1213968-storm-pucks",
        slug="1213968-storm-pucks",
    )
    base.update(kwargs)
    return Project(**base)


def test_hackathon_tokens_strip_year():
    toks = hackathon_tokens("https://stormhacks2026.devpost.com")
    assert "stormhacks2026" in toks
    assert "stormhacks" in toks


def test_project_tokens_ignore_leading_id_and_stopwords():
    toks = project_tokens(_project())
    assert "storm" in toks or "stormpucks" in toks
    assert "pucks" in toks or "stormpucks" in toks
    # Leading submission id digits should not appear as a standalone token
    assert not any(t.isdigit() for t in toks)


def test_project_tokens_skip_tagline():
    p = _project(tagline="A robot for open rails data systems")
    toks = project_tokens(p)
    assert "robot" not in toks
    assert "rails" not in toks


def test_relevance_needles_longest_first():
    needles = relevance_needles(
        _project(), "https://stormhacks2026.devpost.com"
    )
    assert needles
    assert needles == sorted(needles, key=len, reverse=True)


def test_find_mentions_short_token_needs_boundary():
    # "rail" must not match inside "trailer"
    assert find_mentions("trailer hitch", ["rail"]) == []
    assert find_mentions("the rail network", ["rail"]) == ["rail"]


def test_find_mentions_long_token_substring():
    assert "stormpucks" in find_mentions(
        "Initial commit for StormPucks hackathon",
        ["stormpucks"],
    )


def test_find_mentions_fuzzy_hackathon_typo_in_repo_name():
    # StormsHacks2026 (extra 's') should still match stormhacks / stormhacks2026
    hits = find_mentions(
        "Eddie-Yoshie/StormsHacks2026",
        ["stormhacks2026", "stormhacks"],
    )
    assert "stormhacks" in hits or "stormhacks2026" in hits


def test_short_devpost_title_still_gets_hackathon_needles():
    # Title "OK" is useless for name_similarity; needles must still include hackathon.
    needles = relevance_needles(
        _project(title="OK", slug="ok"),
        "https://stormhacks2026.devpost.com",
    )
    assert "stormhacks" in needles or "stormhacks2026" in needles
    assert find_mentions("StormsHacks2026", needles)


def test_name_similarity_exact_and_partial():
    p = _project(title="Cool App", slug="cool-app")
    assert name_similarity("alice/cool-app", p) == 1.0
    assert name_similarity("alice/cool-app-backend", p) >= 0.85
    assert name_similarity("alice/unrelated-thing", p) < 0.5


def test_name_similarity_strips_submission_id_prefix():
    p = _project()
    assert name_similarity("team/storm-pucks", p) >= 0.85
