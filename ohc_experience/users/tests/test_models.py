from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ohc_experience.users.models import User


def test_user_get_absolute_url(user: User):
    # A user's canonical page is their own settings screen, not a public
    # per-pk profile — there is no such thing as viewing another user here.
    assert user.get_absolute_url() == "/settings/profile/"
