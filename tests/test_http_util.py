from unittest.mock import MagicMock

from vibe_check.http_util import HttpClient


class _FakeResp:
    def __init__(self, status_code: int, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}


def test_get_retries_transient_http_status(monkeypatch):
    client = HttpClient()
    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_get(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 3:
            return _FakeResp(503)
        return _FakeResp(200)

    monkeypatch.setattr(client.session, "get", fake_get)
    monkeypatch.setattr("vibe_check.http_util.time.sleep", lambda s: sleeps.append(s))

    resp = client.get("https://example.devpost.com/x", tries=4)
    assert resp.status_code == 200
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_get_retries_429_respects_retry_after(monkeypatch):
    client = HttpClient()
    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_get(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp(429, {"Retry-After": "2"})
        return _FakeResp(200)

    monkeypatch.setattr(client.session, "get", fake_get)
    monkeypatch.setattr("vibe_check.http_util.time.sleep", lambda s: sleeps.append(s))

    resp = client.get("https://example.devpost.com/x", tries=3)
    assert resp.status_code == 200
    assert sleeps == [2]


def test_get_returns_last_transient_after_exhaustion(monkeypatch):
    client = HttpClient()
    monkeypatch.setattr(client.session, "get", lambda *_a, **_k: _FakeResp(502))
    monkeypatch.setattr("vibe_check.http_util.time.sleep", lambda _s: None)
    monkeypatch.setattr(client, "reset", MagicMock())

    resp = client.get("https://example.devpost.com/x", tries=2)
    assert resp.status_code == 502
