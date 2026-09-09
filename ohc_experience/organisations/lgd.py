"""PIN code lookup through the government's Local Government Directory service."""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import re
from datetime import UTC
from datetime import datetime
from http import HTTPStatus
from urllib.parse import urlencode
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.core.cache.backends.locmem import LocMemCache
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_NAME_LENGTH = 120
MAX_CODE_LENGTH = 20
MAX_TIMEOUT_SECONDS = 30
MAX_CACHE_TTL_SECONDS = 24 * 60 * 60

# Independent of the application's cache backend, each worker keeps at most 512
# PIN codes. A failed lookup is never cached, so a retry can recover immediately.
_lookup_cache = LocMemCache(
    "organisation-lgd-pincodes",
    {"OPTIONS": {"MAX_ENTRIES": 512}},
)


class LGDLookupError(Exception):
    """The lookup could not be completed; safe to display to a user."""

    def __init__(self):
        super().__init__(
            _("Location lookup is unavailable. Please try again in a moment."),
        )


def _configuration() -> tuple[str, str, float, int]:
    url = settings.LGD_API_URL
    api_key = settings.LGD_API_KEY
    if not isinstance(url, str) or not isinstance(api_key, str):
        raise LGDLookupError
    url = url.strip().rstrip("/")
    api_key = api_key.strip()
    try:
        parsed = urlsplit(url)
        timeout = float(settings.LGD_API_TIMEOUT)
        cache_ttl = int(settings.LGD_CACHE_TTL)
        valid_url = (
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )
        if (
            not valid_url
            or not api_key
            or not math.isfinite(timeout)
            or not 0 < timeout <= MAX_TIMEOUT_SECONDS
            or not 0 <= cache_ttl <= MAX_CACHE_TTL_SECONDS
        ):
            raise LGDLookupError
    except (TypeError, ValueError) as exc:
        raise LGDLookupError from exc
    return url, api_key, timeout, cache_ttl


def _fetch_locations(url: str, api_key: str, pincode: str, timeout: float):
    """Fetch a bounded JSON response without following redirects with the key."""
    connection = None
    try:
        parsed = urlsplit(url)
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            port=parsed.port,
            timeout=timeout,
        )
        query = urlencode({"pinCode": pincode, "view": "All"})
        connection.request(
            "GET",
            f"{parsed.path}/search?{query}",
            headers={
                "apikey": api_key,
                "REQUEST-ID": str(uuid4()),
                "TIMESTAMP": datetime.now(UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        response = connection.getresponse()
        if response.status != HTTPStatus.OK:
            raise LGDLookupError
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise LGDLookupError
        return json.loads(body)
    except (http.client.HTTPException, OSError, ValueError) as exc:
        # Provider error bodies and credentials must not reach browser responses.
        raise LGDLookupError from exc
    finally:
        if connection is not None:
            connection.close()


def _name(record: dict, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str):
        raise LGDLookupError
    value = value.strip()
    if (
        not value
        or len(value) > MAX_NAME_LENGTH
        or value.upper() == "UNKNOWN"
        or not value.isprintable()
    ):
        raise LGDLookupError
    return value


def _code(record: dict, field: str) -> str:
    value = record.get(field)
    if type(value) not in (str, int):
        raise LGDLookupError
    value = str(value).strip()
    if len(value) > MAX_CODE_LENGTH or not re.fullmatch(r"[1-9][0-9]*", value):
        raise LGDLookupError
    return value


def _normalize_locations(payload) -> list[dict[str, str]]:
    if not isinstance(payload, list):
        raise LGDLookupError
    locations = {}
    for record in payload:
        if not isinstance(record, dict):
            raise LGDLookupError
        location = {
            "state": _name(record, "stateName"),
            "state_code": _code(record, "stateCode"),
            "district": _name(record, "districtName"),
            "district_code": _code(record, "districtCode"),
        }
        key = (location["state_code"], location["district_code"])
        locations.setdefault(key, location)
    return list(locations.values())


def lookup_pincode(pincode: str) -> list[dict[str, str]]:
    """Return distinct state/district matches, or [] for a valid, unknown PIN."""
    if not isinstance(pincode, str) or not re.fullmatch(r"[1-9][0-9]{5}", pincode):
        raise ValidationError(_("Enter a valid six-digit Indian PIN code."))
    url, api_key, timeout, cache_ttl = _configuration()
    # Changing the environment or rotating a key must not reuse old results.
    environment = hashlib.sha256(f"{url}\0{api_key}".encode()).hexdigest()
    cache_key = f"lgd-pincode:{environment}:{pincode}"
    cached = _lookup_cache.get(cache_key)
    if cached is not None:
        return cached
    locations = _normalize_locations(_fetch_locations(url, api_key, pincode, timeout))
    _lookup_cache.set(cache_key, locations, timeout=cache_ttl)
    return locations
