from __future__ import annotations

from typing import TYPE_CHECKING

import bcrypt
from django.contrib.auth import get_user_model

if TYPE_CHECKING:
    from ohc_experience.users.models import User


def test_user_get_absolute_url(user: User):
    # A user's canonical page is their own settings screen, not a public
    # per-pk profile — there is no such thing as viewing another user here.
    assert user.get_absolute_url() == "/settings/profile/"


def test_legacy_bcrypt_hash_verifies_and_upgrades(db, settings):
    # Accounts imported from the legacy sandbox arrive as Spring bcrypt hashes
    # stored verbatim under Django's "bcrypt$" prefix. The verify-only hasher
    # at the end of PASSWORD_HASHERS accepts them; argon2 at the front replaces
    # the hash on the first successful check, so no bcrypt hash outlives a login.
    settings.PASSWORD_HASHERS = [
        "django.contrib.auth.hashers.Argon2PasswordHasher",
        "django.contrib.auth.hashers.BCryptPasswordHasher",
    ]
    salt = bcrypt.gensalt(rounds=4, prefix=b"2a")
    legacy = bcrypt.hashpw(b"Password@123", salt).decode()
    user = get_user_model().objects.create(
        email="legacy@example.com",
        password=f"bcrypt${legacy}",
    )

    assert not user.check_password("wrong")
    assert user.check_password("Password@123")
    user.refresh_from_db()
    assert user.password.startswith("argon2$")
    assert user.check_password("Password@123")
