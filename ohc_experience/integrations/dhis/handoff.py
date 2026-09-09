"""Encode the archived sandbox's JWT and AES envelope without browser secrets.

The archived sandbox uses a fixed IV and AES-128-CBC around an HS256 JWT.
These choices preserve its wire format; current receiver acceptance is unverified.
The caller owns authorization, eligibility and the complete token payload.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers import algorithms
from cryptography.hazmat.primitives.ciphers import modes

if TYPE_CHECKING:
    from collections.abc import Mapping

DHIS_DESTINATION_URL = "https://dhis.abdm.gov.in/DHIS/"
_AES_BYTES = 16


class DHISConfigurationError(ValueError):
    """The handoff cannot safely use the supplied DHIS configuration."""


@dataclass(frozen=True)
class DHISConfig:
    signing_secret: str = field(repr=False)
    encryption_key: str = field(repr=False)
    encryption_iv: str = field(repr=False)
    destination_url: str = DHIS_DESTINATION_URL

    def __post_init__(self) -> None:
        _secret_bytes(self.signing_secret, "DHIS signing secret")
        for value, label in (
            (self.encryption_key, "DHIS encryption key"),
            (self.encryption_iv, "DHIS encryption IV"),
        ):
            if len(_secret_bytes(value, label, encoding="ISO-8859-1")) != _AES_BYTES:
                msg = f"{label} must be exactly 16 ISO-8859-1 bytes."
                raise DHISConfigurationError(msg)
        # An exact match also rejects credentials, query strings, fragments,
        # alternate ports, lookalike hosts and URL parser normalization tricks.
        if self.destination_url != DHIS_DESTINATION_URL:
            msg = "DHIS destination must be the approved HTTPS portal URL."
            raise DHISConfigurationError(msg)


def _secret_bytes(value: str, label: str, *, encoding: str = "UTF-8") -> bytes:
    if not isinstance(value, str) or not value.strip():
        msg = f"{label} must be configured."
        raise DHISConfigurationError(msg)
    try:
        return value.encode(encoding)
    except UnicodeEncodeError:
        msg = f"{label} must contain valid {encoding} text."
        raise DHISConfigurationError(msg) from None


def _jwt_segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def build_dhis_url(payload: Mapping[str, object], *, config: DHISConfig) -> str:
    """Return a handoff URL for an already authorized, server-built payload.

    Match expo-jwt's compact UTF-8 JSON and HS256 header. Do not add claims:
    the legacy generatedTime value and WASA fields belong to the caller.
    Never log this URL, since it contains a bearer handoff and personal data.
    """
    header = _jwt_segment(b'{"typ":"JWT","alg":"HS256"}')
    claims = json.dumps(
        dict(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    signing_input = f"{header}.{_jwt_segment(claims)}".encode("ascii")
    signature = hmac.new(
        config.signing_secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    token = signing_input + b"." + _jwt_segment(signature).encode("ascii")

    # Java's PKCS5PADDING for AES is PKCS#7 with the AES block size (128 bits).
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded_token = padder.update(token) + padder.finalize()
    iv = config.encryption_iv.encode("ISO-8859-1")
    encryptor = Cipher(
        algorithms.AES(config.encryption_key.encode("ISO-8859-1")),
        modes.CBC(iv),
    ).encryptor()
    ciphertext = encryptor.update(padded_token) + encryptor.finalize()
    # Unlike JWT segments, Java's URL encoder retains '=' padding here.
    envelope = ":".join(
        base64.urlsafe_b64encode(part).decode("ascii") for part in (ciphertext, iv)
    )
    return f"{config.destination_url}?{urlencode({'value': envelope})}"
