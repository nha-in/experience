"""Encryption at rest for sandbox client secrets.

A secret has to be shown back to the integrator, so it cannot be hashed; it
is Fernet-encrypted instead. The key is ``ABDM_SECRET_ENCRYPTION_KEY`` when the
deployment sets one, otherwise it is derived from ``SECRET_KEY`` — rotating
``SECRET_KEY`` without setting the dedicated key would make every stored
secret unreadable, which is why the dedicated setting exists.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings


def _fernet() -> Fernet:
    material = settings.ABDM_SECRET_ENCRYPTION_KEY or settings.SECRET_KEY
    digest = hashlib.sha256(material.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
