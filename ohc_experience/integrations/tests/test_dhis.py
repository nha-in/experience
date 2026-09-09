"""Legacy handoff compatibility using synthetic keys and an OpenSSL fixture."""

import base64
import hashlib
import hmac
import json
from dataclasses import replace
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers import algorithms
from cryptography.hazmat.primitives.ciphers import modes

from ohc_experience.integrations.dhis import DHIS_DESTINATION_URL
from ohc_experience.integrations.dhis import DHISConfig
from ohc_experience.integrations.dhis import DHISConfigurationError
from ohc_experience.integrations.dhis import build_dhis_url

CONFIG = DHISConfig(
    signing_secret="synthetic-signing-secret-for-tests",  # noqa: S106
    encryption_key="SyntheticAESKey!",
    encryption_iv="SyntheticInitVec",
)
PAYLOAD = {
    "name": "Synthetic Δ",
    "intent_request": "HMIS",
    "integration_level": "M3",
    "generatedTime": "17200000000000",
}
# Generated independently with OpenSSL HMAC-SHA256 and enc -aes-128-cbc.
# Compact JSON uses expo-jwt's header order and UTF-8 encoding.
REFERENCE_TOKEN = (
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9."  # noqa: S105 - synthetic fixture
    "eyJuYW1lIjoiU3ludGhldGljIM6UIiwiaW50ZW50X3JlcXVlc3QiOiJITUlTIiwiaW50ZWdy"
    "YXRpb25fbGV2ZWwiOiJNMyIsImdlbmVyYXRlZFRpbWUiOiIxNzIwMDAwMDAwMDAwMCJ9."
    "xeIrmkm2-CDlXPfWzfmdg6PZFcD12jgWCwGiY12GSWI"
)
REFERENCE_CIPHERTEXT = (
    "dqiiEKskGNDFy8USOSAiRuNuhEt6LUyIFzBkJ0E7c44hRmIdYMXdpYNaUnhQkicyT32mByo"
    "VvMIYUq3FmILUgSNEUwnm4hnbeQhB8tiGG-CpsQWKyJKblSpTL_q_FRpgL7I6UztBdX-FfY"
    "jDyJ5t2azVaQ3D3NI2qKiGmMysizd-BlPg5IL2-VVbKoSqcJpdmHwLJ96sI2u8eEraabsvPC"
    "azXatHqbZSwsPIH46UUutBCaVizcRZo2tpGpgwhbppoZPmfS0KBhfCogSo5QaoLXnSsl51jeHI"
    "tcQe1YFrUGo="
)
REFERENCE_IV = "U3ludGhldGljSW5pdFZlYw=="


def decrypt_token(url):
    ciphertext_part, iv_part = parse_qs(urlsplit(url).query)["value"][0].split(":")
    decryptor = Cipher(
        algorithms.AES(CONFIG.encryption_key.encode()),
        modes.CBC(base64.urlsafe_b64decode(iv_part)),
    ).decryptor()
    padded = decryptor.update(base64.urlsafe_b64decode(ciphertext_part))
    padded += decryptor.finalize()
    pad_length = padded[-1]
    assert 1 <= pad_length <= algorithms.AES.block_size // 8
    assert padded[-pad_length:] == bytes([pad_length]) * pad_length
    return padded[:-pad_length].decode()


def decode_segment(segment):
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def test_matches_independent_legacy_envelope_fixture():
    url = build_dhis_url(PAYLOAD, config=CONFIG)

    assert url == (
        f"{DHIS_DESTINATION_URL}?value="
        f"{REFERENCE_CIPHERTEXT[:-1]}%3D%3A{REFERENCE_IV[:-2]}%3D%3D"
    )
    assert decrypt_token(url) == REFERENCE_TOKEN


def test_signed_claims_are_preserved_without_adding_timestamp_fields():
    payload = PAYLOAD | {
        "wasa_valid_upto_date": [
            {"milestone": "M3", "expiryDate": "2030-01-01", "status": "Approved"},
        ],
    }
    original_payload = json.loads(json.dumps(payload))

    token = decrypt_token(build_dhis_url(payload, config=CONFIG))
    header, claims, signature = token.split(".")

    assert json.loads(decode_segment(header)) == {"typ": "JWT", "alg": "HS256"}
    assert json.loads(decode_segment(claims)) == original_payload
    assert payload == original_payload
    expected_signature = hmac.digest(
        CONFIG.signing_secret.encode(),
        f"{header}.{claims}".encode(),
        hashlib.sha256,
    )
    assert hmac.compare_digest(decode_segment(signature), expected_signature)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("signing_secret", ""),
        ("signing_secret", "   "),
        ("signing_secret", None),
        ("signing_secret", "invalid\ud800"),
        ("encryption_key", ""),
        ("encryption_key", None),
        ("encryption_key", "short"),
        ("encryption_key", "x" * 24),
        ("encryption_key", "x" * 32),
        ("encryption_key", "Δ" * 16),
        ("encryption_key", " " * 16),
        ("encryption_iv", ""),
        ("encryption_iv", None),
        ("encryption_iv", "short"),
        ("encryption_iv", "x" * 17),
        ("encryption_iv", "Δ" * 16),
    ],
)
def test_rejects_missing_or_malformed_key_material(field_name, value):
    with pytest.raises(DHISConfigurationError):
        replace(CONFIG, **{field_name: value})


@pytest.mark.parametrize(
    "destination",
    [
        "",
        None,
        "http://dhis.abdm.gov.in/DHIS/",
        "https://example.test/DHIS/",
        "https://dhis.abdm.gov.in.evil.test/DHIS/",
        "https://dhis.abdm.gov.in@evil.test/DHIS/",
        "https://user:password@dhis.abdm.gov.in/DHIS/",
        "https://dhis.abdm.gov.in:8443/DHIS/",
        "https://dhis.abdm.gov.in/other/",
        "https://dhis.abdm.gov.in/DHIS/?next=https://example.test",
        "https://dhis.abdm.gov.in/DHIS/#fragment",
        "https://dhis.abdm.gov.in/DHIS/\r\n",
        "//dhis.abdm.gov.in/DHIS/",
    ],
)
def test_rejects_unapproved_destinations(destination):
    with pytest.raises(DHISConfigurationError, match="approved HTTPS"):
        replace(CONFIG, destination_url=destination)


def test_configuration_repr_does_not_disclose_secrets():
    rendered = repr(CONFIG)
    assert CONFIG.signing_secret not in rendered
    assert CONFIG.encryption_key not in rendered
    assert CONFIG.encryption_iv not in rendered


def test_accepts_latin1_key_material_matching_java_encoding():
    config = replace(CONFIG, encryption_key="é" * 16, encryption_iv="à" * 16)

    url = build_dhis_url(PAYLOAD, config=config)

    encoded_iv = parse_qs(urlsplit(url).query)["value"][0].split(":")[1]
    assert base64.urlsafe_b64decode(encoded_iv) == b"\xe0" * 16


def test_rejects_non_json_numbers_in_the_payload():
    with pytest.raises(ValueError, match="Out of range float"):
        build_dhis_url({"generatedTime": float("nan")}, config=CONFIG)
