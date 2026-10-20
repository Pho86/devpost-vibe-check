from __future__ import annotations

import time
from typing import Any

import requests
from requests.exceptions import RequestException

from .config import REQUEST_TIMEOUT, USER_AGENT

# Transient server / rate-limit responses worth retrying (Devpost + similar).
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class HttpClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def reset(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: int = REQUEST_TIMEOUT,
        tries: int = 6,
    ) -> requests.Response:
        last_err: Exception | None = None
        last_resp: requests.Response | None = None
        for attempt in range(1, tries + 1):
            try:
                resp = self.session.get(url, headers=headers, params=params, timeout=timeout)
                if resp.status_code not in _RETRY_STATUS:
                    return resp
                last_resp = resp
                if attempt >= tries:
                    return resp
                retry_after = resp.headers.get("Retry-After")
                if retry_after and str(retry_after).isdigit():
                    wait = max(int(retry_after), 1)
                else:
                    wait = min(2**attempt, 45)
                print(
                    f"  HTTP {resp.status_code}, retry {attempt}/{tries} in {wait}s",
                    flush=True,
                )
                time.sleep(wait)
            except RequestException as exc:
                last_err = exc
                if attempt >= tries:
                    break
                wait = min(2**attempt, 45)
                print(
                    f"  connection error ({exc.__class__.__name__}), "
                    f"retry {attempt}/{tries} in {wait}s",
                    flush=True,
                )
                self.reset()
                time.sleep(wait)
        if last_resp is not None:
            return last_resp
        raise last_err  # type: ignore[misc]
