"""The sandbox page: what a ready sandbox hands the team."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.test import override_settings
from django.urls import reverse

from ohc_experience.organisations.models import Sandbox

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from ohc_experience.organisations.models import Membership
    from ohc_experience.users.models import User

pytestmark = pytest.mark.django_db

SANDBOX_URL = reverse("organisations:sandbox")
FRONTEND_URL = "https://sandbox.example.test"
API_URL = "https://api.example.test"
# The primary credential's row, plus one row per sandbox user.
SECRET_CONTROLS = 3


@pytest.fixture
def ready_sandbox(owner_membership: Membership) -> Sandbox:
    return Sandbox.objects.create(
        organisation=owner_membership.organisation,
        status=Sandbox.Status.READY,
        result={
            "server": API_URL,
            "loaded_data": {"patients": 250, "encounters": 1200, "is_empty": False},
            "users": [
                {
                    "username": "sunrise-admin",
                    "password": "shared-sandbox-password",
                    "role": "Facility Admin",
                    "is_primary": True,
                },
                {
                    "username": "sunrise-nurse",
                    "password": "shared-sandbox-password",
                    "role": "Nurse",
                },
            ],
        },
    )


@override_settings(CARE_SANDBOX_FRONTEND_URL=FRONTEND_URL)
def test_the_server_shown_is_the_frontend_not_the_plugin_api(
    sign_in: Callable[[User], Client],
    owner_membership: Membership,
    ready_sandbox: Sandbox,
):
    response = sign_in(owner_membership.user).get(SANDBOX_URL)
    html = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert FRONTEND_URL in html
    assert API_URL not in html


@override_settings(CARE_SANDBOX_FRONTEND_URL=FRONTEND_URL)
def test_the_sandbox_opens_in_a_new_tab(
    sign_in: Callable[[User], Client],
    owner_membership: Membership,
    ready_sandbox: Sandbox,
):
    response = sign_in(owner_membership.user).get(SANDBOX_URL)
    html = response.content.decode()

    assert f'href="{FRONTEND_URL}"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html


@override_settings(CARE_SANDBOX_FRONTEND_URL="")
def test_an_unconfigured_frontend_leaves_the_link_out(
    sign_in: Callable[[User], Client],
    owner_membership: Membership,
    ready_sandbox: Sandbox,
):
    response = sign_in(owner_membership.user).get(SANDBOX_URL)
    html = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert FRONTEND_URL not in html


@override_settings(CARE_SANDBOX_FRONTEND_URL=FRONTEND_URL)
def test_every_user_row_carries_its_username_and_the_shared_password(
    sign_in: Callable[[User], Client],
    owner_membership: Membership,
    ready_sandbox: Sandbox,
):
    response = sign_in(owner_membership.user).get(SANDBOX_URL)
    html = response.content.decode()

    for username in ("sunrise-admin", "sunrise-nurse"):
        assert f'data-copy="{username}"' in html
    assert html.count('data-copy="shared-sandbox-password"') == SECRET_CONTROLS
    assert html.count('aria-pressed="false"') == SECRET_CONTROLS


@override_settings(CARE_SANDBOX_FRONTEND_URL=FRONTEND_URL)
def test_loaded_data_lists_counts_and_drops_flags(
    sign_in: Callable[[User], Client],
    owner_membership: Membership,
    ready_sandbox: Sandbox,
):
    response = sign_in(owner_membership.user).get(SANDBOX_URL)
    html = response.content.decode()

    assert "Patients" in html
    assert "Encounters" in html
    assert "Is Empty" not in html
