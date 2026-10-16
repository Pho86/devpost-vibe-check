from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from vibe_check.publish import (
    new_share_code,
    publish_review_site,
    write_share_sidecar,
)


def test_new_share_code_is_hex():
    code = new_share_code()
    assert len(code) == 8
    int(code, 16)


def test_publish_creates_site_and_returns_url(tmp_path: Path):
    review = tmp_path / "review"
    review.mkdir()
    (review / "index.html").write_text("<html>ok</html>", encoding="utf-8")

    create_resp = MagicMock(status_code=200)
    create_resp.json.return_value = {"id": "site-1", "name": "vibe-abcd1234"}
    create_resp.text = ""

    deploy_create = MagicMock(status_code=200)
    deploy_create.json.return_value = {
        "id": "deploy-1",
        "required": [],
        "ssl_url": "https://vibe-abcd1234.netlify.app",
        "deploy_ssl_url": "https://deploy-1--vibe-abcd1234.netlify.app",
    }
    deploy_create.text = ""

    ready_resp = MagicMock(status_code=200)
    ready_resp.json.return_value = {
        "id": "deploy-1",
        "state": "ready",
        "ssl_url": "https://vibe-abcd1234.netlify.app",
        "deploy_ssl_url": "https://deploy-1--vibe-abcd1234.netlify.app",
    }
    ready_resp.text = ""

    session = MagicMock()
    session.post.side_effect = [create_resp, deploy_create]
    session.get.return_value = ready_resp
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)

    with patch("vibe_check.publish.requests.Session", return_value=session):
        result = publish_review_site(review, token="nfp_test", code="abcd1234")

    assert result.code == "abcd1234"
    assert result.url == "https://vibe-abcd1234.netlify.app"
    assert result.site_id == "site-1"
    # create site + create deploy (JSON file digest)
    assert session.post.call_count == 2
    sidecar = write_share_sidecar(review, result)
    assert "abcd1234" in sidecar.read_text(encoding="utf-8")


def test_publish_requires_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    review = tmp_path / "review"
    review.mkdir()
    (review / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    monkeypatch.delenv("NETLIFY_AUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="NETLIFY_AUTH_TOKEN"):
        publish_review_site(review, token="")
