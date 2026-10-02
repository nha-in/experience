"""ABDM-wide figures for the landing page, from the ABDM dashboard's KPI service.

A daily task fetches them into the cache, and the landing page reads the cache.
Only before the first fetch, say on a new deployment or a local server with no
Celery worker, does one visit fetch them, and at most once every ten minutes.
"""

from __future__ import annotations

import http.client
import json
import logging
import re
from http import HTTPStatus
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_KEY = "pages:abdm-dashboard-figures"
BOOTSTRAP_LOCK_KEY = "pages:abdm-dashboard-figures:bootstrap"
BOOTSTRAP_INTERVAL_SECONDS = 10 * 60
BOOTSTRAP_TIMEOUT_SECONDS = 5
TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 64 * 1024

# The service's name for each figure the landing page shows.
FIELDS = {
    "records_linked": "HRL Count",
    "professionals_registered": "HPR Count",
    "facilities_registered": "HFR Count",
}


class DashboardUnavailableError(Exception):
    """The KPI service gave no usable figures. The message never holds a credential."""


def _post(url: str, body: dict, timeout: float) -> object:
    """POST JSON and parse a bounded reply; a redirect is refused, not followed."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        msg = "ABDM_DASHBOARD_KPI_URL must be an https URL."
        raise DashboardUnavailableError(msg)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    connection = http.client.HTTPSConnection(
        parsed.hostname,
        port=parsed.port,
        timeout=timeout,
    )
    try:
        connection.request(
            "POST",
            target,
            body=json.dumps(body),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        response = connection.getresponse()
        if response.status != HTTPStatus.OK:
            msg = f"The KPI service answered HTTP {response.status}."
            raise DashboardUnavailableError(msg)
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            msg = "The KPI service's reply is too large."
            raise DashboardUnavailableError(msg)
        return json.loads(raw)
    except (http.client.HTTPException, OSError, ValueError) as exc:
        msg = f"The KPI service could not be read: {type(exc).__name__}."
        raise DashboardUnavailableError(msg) from exc
    finally:
        connection.close()


def parse_count(value: object) -> int | None:
    """A count as the service writes it, "1,22,47,14,978", or a plain number."""
    if isinstance(value, str) and re.fullmatch(r"[0-9][0-9,]*", value.strip()):
        return int(value.strip().replace(",", ""))
    if type(value) is int and value >= 0:
        return value
    return None


def _count(payload: dict, field: str) -> int:
    count = parse_count(payload.get(field))
    if count is None:
        msg = f"The KPI service sent no usable {field!r}."
        raise DashboardUnavailableError(msg)
    return count


def fetch_figures(timeout: float = TIMEOUT_SECONDS) -> dict[str, int]:
    """Ask the KPI service for the landing page's figures."""
    client_id = settings.PMJAY_CLIENT_ID.strip()
    client_secret = settings.PMJAY_CLIENT_SECRET.strip()
    if not client_id or not client_secret:
        msg = "PMJAY_CLIENT_ID and PMJAY_CLIENT_SECRET are not set."
        raise DashboardUnavailableError(msg)
    payload = _post(
        settings.ABDM_DASHBOARD_KPI_URL,
        {
            "clientId": client_id,
            "clientSecret": client_secret,
            "grantType": "client_credentials",
        },
        timeout,
    )
    if not isinstance(payload, dict):
        msg = "The KPI service's reply is not an object."
        raise DashboardUnavailableError(msg)
    try:
        return {name: _count(payload, field) for name, field in FIELDS.items()}
    except DashboardUnavailableError:
        # A refused request still answers 200, with the reason in "status".
        status = payload.get("status")
        if isinstance(status, str) and status.strip():
            msg = f"The KPI service refused the request: {status.strip()[:200]}"
            raise DashboardUnavailableError(msg) from None
        raise


def store_figures(figures: dict[str, int]) -> None:
    """Keep the figures until a successful fetch replaces them."""
    # No expiry: when the service is down, the last good figures stay on the page.
    cache.set(CACHE_KEY, figures, timeout=None)


def refresh_figures(timeout: float = TIMEOUT_SECONDS) -> dict[str, int]:
    """Fetch the figures and keep them until the next refresh replaces them."""
    figures = fetch_figures(timeout)
    store_figures(figures)
    return figures


def cached_figures() -> dict[str, int] | None:
    """The last stored figures, or None before any were fetched or set."""
    figures = cache.get(CACHE_KEY)
    if (
        not isinstance(figures, dict)
        or set(figures) != set(FIELDS)
        or any(type(count) is not int or count < 0 for count in figures.values())
    ):
        return None
    return figures


def current_figures() -> dict[str, int] | None:
    """The cached figures, fetched here only when nothing has been cached yet."""
    figures = cached_figures()
    if figures is None and cache.add(
        BOOTSTRAP_LOCK_KEY,
        value=True,
        timeout=BOOTSTRAP_INTERVAL_SECONDS,
    ):
        try:
            figures = refresh_figures(BOOTSTRAP_TIMEOUT_SECONDS)
        except DashboardUnavailableError as error:
            logger.warning("ABDM dashboard figures are unavailable: %s", error)
    return figures
