"""Publish the static review site to Netlify under a unique share code."""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests

NETLIFY_API = "https://api.netlify.com/api/v1"

# Netlify zip deploys sometimes serve index.html as text/plain; force HTML.
_HEADERS_FILE = """\
/
  Content-Type: text/html; charset=utf-8

/index.html
  Content-Type: text/html; charset=utf-8
"""


@dataclass(frozen=True)
class PublishResult:
    code: str
    url: str
    site_id: str
    deploy_id: str


def new_share_code(*, nbytes: int = 4) -> str:
    """Short hex code for vibe-<code>.netlify.app (a-z0-9 only)."""
    return secrets.token_hex(nbytes)


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": "devpost-vibe-check-publish",
    }


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _review_files(review_dir: Path) -> dict[str, bytes]:
    """Path (with leading /) → bytes for deploy."""
    review_dir = Path(review_dir)
    files: dict[str, bytes] = {
        "/_headers": _HEADERS_FILE.encode("utf-8"),
    }
    for path in sorted(review_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name in {"share.txt"}:
            continue
        rel = "/" + path.relative_to(review_dir).as_posix()
        files[rel] = path.read_bytes()
    if "/index.html" not in files:
        raise FileNotFoundError(f"Review site not found: {review_dir / 'index.html'}")
    return files


def _create_site(session: requests.Session, token: str, name: str) -> dict:
    resp = session.post(
        f"{NETLIFY_API}/sites",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"name": name},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Netlify create site failed ({resp.status_code}): {resp.text[:400]}"
        )
    return resp.json()


def _deploy_files(
    session: requests.Session,
    token: str,
    site_id: str,
    files: dict[str, bytes],
) -> dict:
    """File-digest deploy so Netlify stores correct content types."""
    digests = {path: _sha1(body) for path, body in files.items()}
    resp = session.post(
        f"{NETLIFY_API}/sites/{site_id}/deploys",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"files": digests},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Netlify create deploy failed ({resp.status_code}): {resp.text[:400]}"
        )
    deploy = resp.json()
    deploy_id = str(deploy.get("id") or "")
    if not deploy_id:
        raise RuntimeError(f"Netlify deploy returned no id: {deploy}")

    required = set(deploy.get("required") or [])
    by_sha = {_sha1(body): (path, body) for path, body in files.items()}
    for sha in required:
        if sha not in by_sha:
            raise RuntimeError(f"Netlify required unknown file sha {sha}")
        path, body = by_sha[sha]
        # API path is without leading slash, URL-encoded.
        api_path = quote(path.lstrip("/"), safe="/")
        content_type = (
            "text/html; charset=utf-8"
            if path.endswith(".html") or path == "/"
            else "application/octet-stream"
        )
        put = session.put(
            f"{NETLIFY_API}/deploys/{deploy_id}/files/{api_path}",
            headers={
                **_auth_headers(token),
                "Content-Type": content_type,
            },
            data=body,
            timeout=120,
        )
        if put.status_code >= 400:
            raise RuntimeError(
                f"Netlify file upload failed for {path} ({put.status_code}): "
                f"{put.text[:300]}"
            )
    return deploy


def _wait_ready(
    session: requests.Session,
    token: str,
    deploy_id: str,
    *,
    timeout_s: float = 90.0,
) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        resp = session.get(
            f"{NETLIFY_API}/deploys/{deploy_id}",
            headers=_auth_headers(token),
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Netlify deploy poll failed ({resp.status_code}): {resp.text[:300]}"
            )
        last = resp.json()
        state = (last.get("state") or "").lower()
        if state == "ready":
            return last
        if state in {"error", "failed"}:
            raise RuntimeError(
                f"Netlify deploy {state}: {last.get('error_message') or last}"
            )
        time.sleep(1.5)
    raise RuntimeError(
        f"Netlify deploy timed out (last state={last.get('state')!r})"
    )


def publish_review_site(
    review_dir: Path,
    *,
    token: str | None = None,
    site_id: str | None = None,
    code: str | None = None,
) -> PublishResult:
    """
    Upload review_dir to Netlify and return a unique share URL.

    - Default: create site ``vibe-<code>`` → ``https://vibe-<code>.netlify.app``
    - If ``NETLIFY_SITE_ID`` / site_id is set: deploy to that site and return the
      unique deploy URL (still a one-off link for co-reviewers).
    """
    review_dir = Path(review_dir)
    token = (token or os.environ.get("NETLIFY_AUTH_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            "Missing NETLIFY_AUTH_TOKEN. Create one at "
            "https://app.netlify.com/user/applications#personal-access-tokens "
            "and put it in .env, then re-run with --publish."
        )

    site_id = (site_id or os.environ.get("NETLIFY_SITE_ID") or "").strip() or None
    code = (code or new_share_code()).lower()
    files = _review_files(review_dir)

    with requests.Session() as session:
        created_site_id = site_id
        if not created_site_id:
            site_name = f"vibe-{code}"
            site = _create_site(session, token, site_name)
            created_site_id = str(site.get("id") or "")
            if not created_site_id:
                raise RuntimeError(f"Netlify create site returned no id: {site}")

        deploy = _deploy_files(session, token, created_site_id, files)
        deploy_id = str(deploy.get("id") or "")
        ready = _wait_ready(session, token, deploy_id)
        if site_id:
            url = (
                ready.get("deploy_ssl_url")
                or ready.get("ssl_url")
                or deploy.get("deploy_ssl_url")
                or deploy.get("ssl_url")
                or ""
            )
        else:
            url = (
                ready.get("ssl_url")
                or ready.get("deploy_ssl_url")
                or f"https://vibe-{code}.netlify.app"
            )

    url = str(url).rstrip("/")
    if not url.startswith("http"):
        raise RuntimeError(f"Netlify returned unexpected URL: {url!r}")

    return PublishResult(
        code=code,
        url=url,
        site_id=created_site_id,
        deploy_id=deploy_id,
    )


def write_share_sidecar(review_dir: Path, result: PublishResult) -> Path:
    """Write share.txt next to index.html for local reference."""
    review_dir = Path(review_dir)
    path = review_dir / "share.txt"
    path.write_text(
        (
            f"code: {result.code}\n"
            f"url: {result.url}\n"
            f"site_id: {result.site_id}\n"
            f"deploy_id: {result.deploy_id}\n"
        ),
        encoding="utf-8",
    )
    return path
