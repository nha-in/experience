"""The PIN lookup boundary: provider normalization, failures, cache and access."""

# ruff: noqa: PLR2004

from __future__ import annotations

import http.client
import json
from datetime import datetime
from http import HTTPStatus
from unittest.mock import Mock
from uuid import UUID

import pytest
from django.core.cache.backends.locmem import LocMemCache
from django.core.exceptions import ValidationError
from django.urls import reverse

from ohc_experience.organisations import lgd
from ohc_experience.organisations.lgd import LGDLookupError
from ohc_experience.organisations.lgd import lookup_pincode
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.users.tests.factories import UserFactory

PINCODE = "560001"
LOOKUP_URL = reverse("organisations:pincode-lookup")
PROVIDER_ROW = {
    "stateName": "KARNATAKA",
    "stateCode": 29,
    "districtName": "BENGALURU URBAN",
    "districtCode": 525,
    "pinCode": 560001,
    "townVillageName": "Bengaluru",
}
LOCATION = {
    "state": "KARNATAKA",
    "state_code": "29",
    "district": "BENGALURU URBAN",
    "district_code": "525",
}


@pytest.fixture(autouse=True)
def lgd_configuration(settings, monkeypatch):
    settings.LGD_API_KEY = "test-provider-key"
    settings.LGD_API_URL = "https://lgd.example.test/internal/lgd"
    settings.LGD_API_TIMEOUT = 5.0
    settings.LGD_CACHE_TTL = 3600
    cache = LocMemCache("lgd-tests", {"OPTIONS": {"MAX_ENTRIES": 512}})
    cache.clear()
    monkeypatch.setattr(lgd, "_lookup_cache", cache)


@pytest.fixture
def provider(monkeypatch):
    connection = Mock()
    connection.getresponse.return_value.status = HTTPStatus.OK
    connection.getresponse.return_value.read.return_value = json.dumps(
        [PROVIDER_ROW],
    ).encode()
    factory = Mock(return_value=connection)
    monkeypatch.setattr(lgd.http.client, "HTTPSConnection", factory)
    return factory, connection


class TestLookupPincode:
    def test_normalizes_provider_and_sends_server_credentials(self, provider):
        factory, connection = provider
        assert lookup_pincode(PINCODE) == [LOCATION]
        factory.assert_called_once_with("lgd.example.test", port=None, timeout=5.0)
        args, kwargs = connection.request.call_args
        assert args == ("GET", "/internal/lgd/search?pinCode=560001&view=All")
        headers = kwargs["headers"]
        assert headers["apikey"] == "test-provider-key"
        assert headers["Content-Type"] == "application/json"
        assert UUID(headers["REQUEST-ID"]).version == 4
        timestamp = headers["TIMESTAMP"]
        assert timestamp.endswith("Z")
        # The provider requires precisely three decimal digits and a Z suffix.
        assert len(timestamp.partition(".")[2]) == 4
        assert datetime.fromisoformat(timestamp).utcoffset().total_seconds() == 0
        connection.close.assert_called_once()

    def test_deduplicates_villages_but_preserves_distinct_districts(self, provider):
        _, connection = provider
        another_district = {
            **PROVIDER_ROW,
            "districtName": "BENGALURU RURAL",
            "districtCode": "526",
        }
        connection.getresponse.return_value.read.return_value = json.dumps(
            [
                PROVIDER_ROW,
                {**PROVIDER_ROW, "townVillageName": "Other"},
                another_district,
            ],
        ).encode()
        assert lookup_pincode(PINCODE) == [
            LOCATION,
            {**LOCATION, "district": "BENGALURU RURAL", "district_code": "526"},
        ]

    @pytest.mark.parametrize(
        "pincode",
        ["", "12345", "1234567", "012345", "56000a", "५६०००१", "560001\n", 560001],
    )
    def test_rejects_invalid_pin_before_network(self, pincode, provider):
        factory, _ = provider
        with pytest.raises(ValidationError, match="six-digit"):
            lookup_pincode(pincode)
        factory.assert_not_called()

    @pytest.mark.parametrize(
        ("setting", "value"),
        [
            ("LGD_API_KEY", ""),
            ("LGD_API_KEY", None),
            ("LGD_API_URL", "http://lgd.example.test"),
            ("LGD_API_URL", "https://name:password@lgd.example.test/lgd"),
            ("LGD_API_TIMEOUT", 0),
            ("LGD_API_TIMEOUT", float("inf")),
            ("LGD_API_TIMEOUT", float("nan")),
            ("LGD_CACHE_TTL", -1),
        ],
    )
    def test_configuration_failure_is_safe(self, settings, provider, setting, value):
        factory, _ = provider
        setattr(settings, setting, value)
        with pytest.raises(LGDLookupError, match="Please try again"):
            lookup_pincode(PINCODE)
        factory.assert_not_called()

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {},
            {"data": [PROVIDER_ROW]},
            [None],
            [{}],
            [{**PROVIDER_ROW, "stateName": ""}],
            [{**PROVIDER_ROW, "districtName": "UNKNOWN"}],
            [{**PROVIDER_ROW, "districtName": "x" * 121}],
            [{**PROVIDER_ROW, "districtCode": None}],
            [{**PROVIDER_ROW, "stateCode": 0}],
            [{**PROVIDER_ROW, "stateCode": True}],
            [{**PROVIDER_ROW, "stateCode": "२९"}],
            [PROVIDER_ROW, {**PROVIDER_ROW, "districtCode": "invalid"}],
        ],
    )
    def test_rejects_malformed_records_without_partial_results(self, provider, payload):
        _, connection = provider
        connection.getresponse.return_value.read.return_value = json.dumps(
            payload,
        ).encode()
        with pytest.raises(LGDLookupError):
            lookup_pincode(PINCODE)

    @pytest.mark.parametrize("status", [302, 400, 401, 429, 500])
    def test_provider_status_is_safe_and_never_redirected(self, provider, status):
        _, connection = provider
        response = connection.getresponse.return_value
        response.status = status
        response.read.return_value = b'{"error":"secret provider details"}'
        with pytest.raises(LGDLookupError, match="Please try again"):
            lookup_pincode(PINCODE)
        response.read.assert_not_called()
        connection.request.assert_called_once()
        connection.close.assert_called_once()

    @pytest.mark.parametrize(
        "failure",
        [TimeoutError, OSError, http.client.HTTPException],
    )
    def test_network_errors_are_safe(self, provider, failure):
        _, connection = provider
        connection.getresponse.side_effect = failure
        with pytest.raises(LGDLookupError, match="Please try again"):
            lookup_pincode(PINCODE)
        connection.close.assert_called_once()

    @pytest.mark.parametrize("body", [b"not JSON", b"\xff", b" " * (1024 * 1024 + 1)])
    def test_rejects_invalid_or_oversized_response(self, provider, body):
        _, connection = provider
        connection.getresponse.return_value.read.return_value = body
        with pytest.raises(LGDLookupError):
            lookup_pincode(PINCODE)

    def test_reuses_success_without_sharing_mutable_results(self, provider):
        _, connection = provider
        first = lookup_pincode(PINCODE)
        first[0]["district"] = "Changed by caller"
        assert lookup_pincode(PINCODE) == [LOCATION]
        connection.request.assert_called_once()

    def test_caches_empty_success(self, provider):
        _, connection = provider
        connection.getresponse.return_value.read.return_value = b"[]"
        assert lookup_pincode(PINCODE) == []
        assert lookup_pincode(PINCODE) == []
        connection.request.assert_called_once()

    def test_does_not_cache_failures(self, provider):
        _, connection = provider
        connection.getresponse.side_effect = [
            TimeoutError,
            connection.getresponse.return_value,
        ]
        with pytest.raises(LGDLookupError):
            lookup_pincode(PINCODE)
        assert lookup_pincode(PINCODE) == [LOCATION]
        assert connection.request.call_count == 2

    @pytest.mark.parametrize("setting", ["LGD_API_URL", "LGD_API_KEY"])
    def test_cache_is_separated_by_environment(self, provider, settings, setting):
        _, connection = provider
        assert lookup_pincode(PINCODE) == [LOCATION]
        setattr(settings, setting, getattr(settings, setting) + "-changed")
        assert lookup_pincode(PINCODE) == [LOCATION]
        assert connection.request.call_count == 2

    def test_zero_ttl_disables_cache(self, provider, settings):
        _, connection = provider
        settings.LGD_CACHE_TTL = 0
        assert lookup_pincode(PINCODE) == [LOCATION]
        assert lookup_pincode(PINCODE) == [LOCATION]
        assert connection.request.call_count == 2


@pytest.mark.django_db
class TestPincodeLookupView:
    def test_requires_login(self, client, provider):
        factory, _ = provider
        response = client.get(LOOKUP_URL, {"pincode": PINCODE})
        assert response.status_code == HTTPStatus.FOUND
        assert reverse("account_login") in response.url
        factory.assert_not_called()

    def test_requires_an_organisation(self, client, provider):
        factory, _ = provider
        client.force_login(UserFactory())
        response = client.get(LOOKUP_URL, {"pincode": PINCODE})
        assert response.status_code == HTTPStatus.FORBIDDEN
        assert "organisation" in response.json()["error"]
        factory.assert_not_called()

    def test_member_can_lookup_before_onboarding(self, client, provider):
        client.force_login(MembershipFactory().user)
        response = client.get(LOOKUP_URL, {"pincode": f" {PINCODE} "})
        assert response.status_code == HTTPStatus.OK
        assert response.json() == {"pincode": PINCODE, "locations": [LOCATION]}
        assert b"test-provider-key" not in response.content

    def test_invalid_pin_returns_json(self, client, provider):
        factory, _ = provider
        client.force_login(MembershipFactory().user)
        response = client.get(LOOKUP_URL, {"pincode": "123"})
        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert "six-digit" in response.json()["error"]
        factory.assert_not_called()

    def test_no_match_returns_not_found(self, client, provider):
        _, connection = provider
        connection.getresponse.return_value.read.return_value = b"[]"
        client.force_login(MembershipFactory().user)
        response = client.get(LOOKUP_URL, {"pincode": PINCODE})
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert "No state or district" in response.json()["error"]

    def test_unavailable_lookup_returns_retry_message(self, client, provider):
        _, connection = provider
        connection.getresponse.side_effect = TimeoutError("secret provider details")
        client.force_login(MembershipFactory().user)
        response = client.get(LOOKUP_URL, {"pincode": PINCODE})
        assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert "Please try again" in response.json()["error"]
        assert b"secret provider details" not in response.content

    def test_post_is_not_allowed(self, client, provider):
        factory, _ = provider
        client.force_login(MembershipFactory().user)
        response = client.post(LOOKUP_URL, {"pincode": PINCODE})
        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
        factory.assert_not_called()
