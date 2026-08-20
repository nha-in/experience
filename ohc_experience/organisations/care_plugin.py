"""Thin client for the Care sandbox plugin (stdlib only, Basic Auth)."""

from __future__ import annotations

import base64
import json
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request
from urllib.request import urlopen

from django.conf import settings

CREATE_PATH = "/api/care_experience_sandbox_be/sandbox/"


class CarePluginError(Exception):
    pass


class CarePluginClient:
    def __init__(self) -> None:
        self.base_url = settings.CARE_SANDBOX_BASE_URL.rstrip("/")
        if urlparse(self.base_url).scheme not in {"http", "https"}:
            msg = "CARE_SANDBOX_BASE_URL must use http or https."
            raise CarePluginError(msg)
        self.username = settings.CARE_SANDBOX_USERNAME
        self.password = settings.CARE_SANDBOX_PASSWORD
        self.timeout = settings.CARE_SANDBOX_TIMEOUT

    def _auth_header(self) -> str:
        token = base64.b64encode(
            f"{self.username}:{self.password}".encode(),
        ).decode()
        return f"Basic {token}"

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(  # noqa: S310 - base URL scheme is validated in __init__.
            f"{self.base_url}{path}",
            data=data,
            method=method,
        )
        req.add_header("Authorization", self._auth_header())
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(  # noqa: S310 - base URL scheme is validated in __init__.
                req,
                timeout=self.timeout,
            ) as resp:
                body = resp.read().decode()
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            msg = f"Care plugin {method} {path} failed ({exc.code}): {detail}"
            raise CarePluginError(msg) from exc
        except URLError as exc:
            msg = f"Care plugin {method} {path} unreachable: {exc.reason}"
            raise CarePluginError(msg) from exc
        return json.loads(body) if body else {}

    def create_sandbox(self, facility_name: str, *, is_facility_empty: bool) -> dict:
        return self._request(
            "POST",
            CREATE_PATH,
            {"facility_name": facility_name, "is_facility_empty": is_facility_empty},
        )

    def get_sandbox(self, job_id: str) -> dict:
        return self._request("GET", f"{CREATE_PATH}{job_id}/")

    def delete_sandbox(self, job_id: str) -> dict:
        return self._request("DELETE", f"{CREATE_PATH}{job_id}/")
