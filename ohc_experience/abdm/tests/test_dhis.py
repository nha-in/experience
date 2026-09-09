"""The ABDM wrapper supplies the legacy clock without mutating trusted data."""

import pytest

from ohc_experience.abdm import dhis
from ohc_experience.integrations.dhis import DHISConfigurationError


@pytest.fixture
def configuration(settings):
    settings.ABDM_DHIS_URL = "https://dhis.abdm.gov.in/DHIS/"
    settings.ABDM_DHIS_JWT_SECRET = "synthetic-jwt-key"  # noqa: S105
    settings.ABDM_DHIS_AES_KEY = "0123456789abcdef"
    settings.ABDM_DHIS_AES_IV = "test-vector-1234"
    return settings


def test_handoff_uses_server_clock_and_config_without_mutating_payload(
    configuration,
    monkeypatch,
):
    monkeypatch.setattr(dhis.time, "time_ns", lambda: 1_789_000_000_123_999_999)
    calls = []

    def encode(payload, *, config):
        calls.append((payload, config))
        return "https://dhis.abdm.gov.in/DHIS/?value=synthetic"

    monkeypatch.setattr(dhis, "build_dhis_url", encode)
    original = {"client_id": "SBXID_TEST", "role": "old", "generatedTime": "stale"}
    result = dhis.create_handoff_url(original)

    assert result == "https://dhis.abdm.gov.in/DHIS/?value=synthetic"
    payload, config = calls[0]
    assert payload == {
        "client_id": "SBXID_TEST",
        "role": "sandbox",
        "generatedTime": "17890000001230",
    }
    assert original == {
        "client_id": "SBXID_TEST",
        "role": "old",
        "generatedTime": "stale",
    }
    assert config.signing_secret == configuration.ABDM_DHIS_JWT_SECRET
    assert config.encryption_key == configuration.ABDM_DHIS_AES_KEY
    assert config.encryption_iv == configuration.ABDM_DHIS_AES_IV
    assert config.destination_url == configuration.ABDM_DHIS_URL


def test_missing_deployment_key_prevents_url_generation(settings):
    settings.ABDM_DHIS_JWT_SECRET = ""
    settings.ABDM_DHIS_AES_KEY = ""
    settings.ABDM_DHIS_AES_IV = ""
    with pytest.raises(DHISConfigurationError):
        dhis.create_handoff_url({"client_id": "SBXID_TEST"})


def test_each_handoff_gets_a_fresh_timestamp(configuration, monkeypatch):
    clock = iter([1_789_000_000_123_000_000, 1_789_000_000_456_000_000])
    monkeypatch.setattr(dhis.time, "time_ns", lambda: next(clock))
    monkeypatch.setattr(
        dhis,
        "build_dhis_url",
        lambda payload, **kwargs: payload["generatedTime"],
    )
    assert dhis.create_handoff_url({}) == "17890000001230"
    assert dhis.create_handoff_url({}) == "17890000004560"
