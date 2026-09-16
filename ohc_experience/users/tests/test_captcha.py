import io
import json
from unittest.mock import patch

import pytest
from django import forms

from ohc_experience.users.captcha import SignupVerificationMixin


class VerificationForm(SignupVerificationMixin, forms.Form):
    pass


@pytest.fixture
def signup_request(rf, settings):
    settings.SANDBOX_SIGNUP_CAPTCHA = True
    settings.TURNSTILE_SITE_KEY = ""
    settings.TURNSTILE_SECRET_KEY = ""
    request = rf.post("/accounts/signup/")
    request.session = {}
    return request


def test_local_development_asks_no_verification_question(signup_request, settings):
    settings.DEBUG = True
    form = VerificationForm(data={}, request=signup_request)

    assert "captcha" not in form.fields
    assert form.is_valid()


def test_production_without_turnstile_fails_closed(signup_request, settings):
    settings.DEBUG = False
    assert not VerificationForm(
        data={"captcha": "anything"},
        request=signup_request,
    ).is_valid()


@pytest.mark.parametrize(
    ("response", "valid"),
    [
        ({"success": True, "hostname": "testserver"}, True),
        ({"success": True, "hostname": "different.example"}, False),
        ({"success": False, "hostname": "testserver"}, False),
    ],
)
def test_turnstile_validates_server_response_and_hostname(
    signup_request,
    settings,
    response,
    valid,
):
    settings.TURNSTILE_SITE_KEY = "test-site"
    settings.TURNSTILE_SECRET_KEY = "test-secret"  # noqa: S105 - mocked provider key
    with patch(
        "ohc_experience.users.captcha.urlopen",
        return_value=io.BytesIO(json.dumps(response).encode()),
    ):
        form = VerificationForm(
            data={"captcha": "response-token"},
            request=signup_request,
        )
        assert form.is_valid() is valid


def test_turnstile_connection_failure_is_not_accepted(signup_request, settings):
    settings.TURNSTILE_SITE_KEY = "test-site"
    settings.TURNSTILE_SECRET_KEY = "test-secret"  # noqa: S105 - mocked provider key
    with patch("ohc_experience.users.captcha.urlopen", side_effect=TimeoutError):
        form = VerificationForm(
            data={"captcha": "response-token"},
            request=signup_request,
        )
        assert not form.is_valid()
