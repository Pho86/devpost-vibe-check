from __future__ import annotations

import time
from typing import Any

import requests
from requests.exceptions import RequestException

from .config import REQUEST_TIMEOUT, USER_AGENT


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
        for attempt in range(1, tries + 1):
            try:
                return self.session.get(
                    url, headers=headers, params=params, timeout=timeout
                )
            except RequestException as exc:
                last_err = exc
                wait = min(2**attempt, 45)
                print(
                    f"  connection error ({exc.__class__.__name__}), "
                    f"retry {attempt}/{tries} in {wait}s",
                    flush=True,
                )
                self.reset()
                time.sleep(wait)
        raise last_err  # type: ignore[misc]
