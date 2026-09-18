"""Resilient NSE HTTP client (Phase 2.1).

`requests.Session` with browser headers, cookie bootstrapping against
www.nseindia.com, tenacity exponential backoff (403/429/5xx, max 5 attempts),
and a transparent local disk cache at data/raw/{YYYY}/{MM}/.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import requests
from tenacity import (retry, retry_if_exception, retry_if_exception_type,
                      stop_after_attempt, wait_exponential_jitter)

log = logging.getLogger("nse_cash.fetcher")

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0"
    ),
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",  # requests auto-decompresses these; br would need the brotli pkg
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else None
        return code in _RETRYABLE_STATUS
    return isinstance(exc, (requests.ConnectionError, requests.Timeout))


import threading

class NSEHttpClient:
    """Session-based client for NSE archives and JSON APIs with disk caching.

    Uses threading.local so worker threads in ThreadPoolExecutor get isolated
    requests.Session instances with thread-safe cookie jars and connections.
    """

    def __init__(self, cache_dir: Path, timeout: float = 20.0) -> None:
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self._local = threading.local()
        self._sessions: list[requests.Session] = []
        self._lock = threading.Lock()

    @property
    def session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            s = requests.Session()
            s.headers.update(BROWSER_HEADERS)
            self._local.session = s
            self._local.needs_bootstrap = True
            with self._lock:
                self._sessions.append(s)
        return self._local.session

    def _ensure_cookies(self) -> None:
        """Bootstrap once per worker session; again after any 403/429."""
        if getattr(self._local, "needs_bootstrap", True):
            self._bootstrap_cookies()
            self._local.needs_bootstrap = False

    # -- tenacity decorated core GET --------------------------------------
    @retry(
        retry=retry_if_exception(_should_retry) | retry_if_exception_type(
            (requests.ConnectionError, requests.Timeout)),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get(self, url: str, stream: bool = False) -> requests.Response:
        # NSE rotates edge cookies mid-run (~tens of minutes); a multi-hour
        # backfill must re-bootstrap before each retry or every request 403s
        # until the process dies.
        self._ensure_cookies()
        resp = self.session.get(url, timeout=self.timeout, stream=stream)
        if resp.status_code in _RETRYABLE_STATUS:
            self._local.needs_bootstrap = True   # fresh cookies before the retry
            resp.raise_for_status()
        resp.raise_for_status()
        return resp

    def _bootstrap_cookies(self) -> None:
        """Hit the homepage first so NSE's edge sets the required cookies."""
        try:
            self.session.get("https://www.nseindia.com/", timeout=self.timeout)
        except requests.RequestException as exc:
            log.warning("cookie bootstrap failed (will retry): %s", exc)
            self._local.needs_bootstrap = True

    # -- public API --------------------------------------------------------
    def get_bytes(self, url: str, cache_path: Optional[Path] = None,
                  bootstrap: bool = False) -> bytes:
        """GET binary content with local disk cache; returns raw bytes."""
        if cache_path is not None and Path(cache_path).exists():
            return Path(cache_path).read_bytes()
        _ = bootstrap  # cookies are ensured inside _get (per-session, self-healing)
        resp = self._get(url)
        content = resp.content
        if cache_path is not None:
            path = Path(cache_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return content

    def get_json(self, url: str, bootstrap: bool = True,
                 referer: str = "https://www.nseindia.com/") -> dict | list:
        """GET a JSON API endpoint (cookie-bootstrapped by default).

        NSE serves an HTML block page when the Accept header or cookie state
        looks wrong; we retry once with a JSON Accept header + API referer
        after a fresh bootstrap.
        """
        try:
            resp = self._get(url)
            return resp.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            self._bootstrap_cookies()
            resp = self.session.get(
                url, timeout=self.timeout,
                headers={"Accept": "application/json, text/plain, */*",
                         "Referer": referer})
            resp.raise_for_status()
            return resp.json()

    def close(self) -> None:
        with self._lock:
            for s in self._sessions:
                try:
                    s.close()
                except Exception:
                    pass
            self._sessions.clear()
