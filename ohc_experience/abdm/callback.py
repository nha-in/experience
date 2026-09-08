"""The callback reachability probe: one HTTP GET, one result.

Stdlib only, like the Care plugin client. Anything but a completed response is
an error string; the monitor stores whichever it got and moves on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request
from urllib.request import urlopen

from django.conf import settings

USER_AGENT = "ABDM-Sandbox-Portal/1.0 (+callback-monitor)"


@dataclass(frozen=True)
class CallbackResult:
    status_code: int | None
    latency_ms: int | None
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.status_code and self.status_code < 400)  # noqa: PLR2004


def probe_callback(url: str, *, timeout: int | None = None) -> CallbackResult:
    if urlparse(url).scheme not in {"http", "https"}:
        return CallbackResult(None, None, "The callback URL must use http or https.")
    request = Request(url, method="GET", headers={"User-Agent": USER_AGENT})  # noqa: S310
    started = time.monotonic()
    try:
        with urlopen(  # noqa: S310 - the scheme is checked above
            request,
            timeout=timeout or settings.ABDM_CALLBACK_TIMEOUT,
        ) as response:
            latency = int((time.monotonic() - started) * 1000)
            return CallbackResult(response.status, latency)
    except HTTPError as exc:
        latency = int((time.monotonic() - started) * 1000)
        return CallbackResult(exc.code, latency, f"HTTP {exc.code}")
    except URLError as exc:
        return CallbackResult(None, None, str(exc.reason)[:255])
    except (TimeoutError, OSError, ValueError) as exc:
        return CallbackResult(None, None, str(exc)[:255] or "Unreachable")
