"""Portal handoffs recheck product eligibility and keep tokens out of audit data."""

# ruff: noqa: F811, PLR2004
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import Mock
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import pytest
from django.contrib.messages import ERROR
from django.contrib.messages import SUCCESS
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm import dhis
from ohc_experience.abdm.tests.test_dhis_product import approve_milestones
from ohc_experience.abdm.tests.test_dhis_product import change_solutions
from ohc_experience.abdm.tests.test_dhis_product import eligible_hmis  # noqa: F401
from ohc_experience.abdm.tests.test_wasa_lifecycle import certificate_data
from ohc_experience.abdm.tests.test_wasa_lifecycle import decide
from ohc_experience.abdm.tests.test_wasa_lifecycle import request_renewal
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.wasa import current_wasa
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.integrations.tests.test_dhis import CONFIG
from ohc_experience.integrations.tests.test_dhis import decode_segment
from ohc_experience.integrations.tests.test_dhis import decrypt_token

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def dhis_configuration(settings):
    settings.ABDM_DHIS_URL = CONFIG.destination_url
    settings.ABDM_DHIS_JWT_SECRET = CONFIG.signing_secret
    settings.ABDM_DHIS_AES_KEY = CONFIG.encryption_key
    settings.ABDM_DHIS_AES_IV = CONFIG.encryption_iv


def handoff_url(environment, key="dhis"):
    return reverse(
        "experiences:product-handoff",
        args=[environment["workspace"].reference, key],
    )


def overview_url(environment):
    return environment["workspace"].get_absolute_url()


def handoff_events(environment):
    return AuditEvent.objects.filter(
        product=environment["workspace"].product,
        action__startswith="DHIS handoff",
    )


def claims(response):
    assert response.status_code == 302
    assert response.url.startswith(f"{CONFIG.destination_url}?value=")
    header, payload, signature = decrypt_token(response.url).split(".")
    expected_signature = hmac.digest(
        CONFIG.signing_secret.encode(),
        f"{header}.{payload}".encode(),
        hashlib.sha256,
    )
    assert hmac.compare_digest(decode_segment(signature), expected_signature)
    return json.loads(decode_segment(payload))


def test_overview_shows_only_eligible_options_without_generating_tokens(
    eligible_hmis,
    client,
    monkeypatch,
):
    encode = Mock(side_effect=AssertionError("Page reads must not create tokens"))
    monkeypatch.setattr(dhis, "create_handoff_url", encode)
    client.force_login(eligible_hmis["applicant"])

    response = client.get(overview_url(eligible_hmis))

    assert response.status_code == 200
    handoffs = response.context["handoffs"]
    assert len(handoffs) == 1
    assert handoffs[0]["key"] == "dhis"
    options = {option["key"]: option for option in handoffs[0]["options"]}
    assert set(options) == {"hmis"}
    assert options["hmis"]["enabled"]
    assert b'aria-label="Continue to DHIS as HMIS"' in response.content
    for label in ("LMIS", "Telemedicine", "Health Locker", "Pharmacy"):
        assert (
            f'aria-label="Continue to DHIS as {label}"'.encode() not in response.content
        )
    assert handoff_url(eligible_hmis).encode() in response.content
    assert b"csrfmiddlewaretoken" in response.content
    encode.assert_not_called()
    assert not handoff_events(eligible_hmis).exists()


def test_overview_hides_dhis_when_no_solution_is_eligible(environment, client):
    client.force_login(environment["applicant"])

    response = client.get(overview_url(environment))

    assert response.status_code == 200
    assert not response.context["handoffs"]
    assert b'id="handoff-dhis-heading"' not in response.content
    assert handoff_url(environment).encode() not in response.content


def test_overview_shows_only_lmis_when_hmis_milestones_are_incomplete(
    environment,
    client,
):
    decide(environment, change_solutions(environment, ["hmis", "lmis"]))
    approve_milestones(environment, ("m1", "m2"))
    client.force_login(environment["applicant"])

    response = client.get(overview_url(environment))

    assert response.status_code == 200
    assert [option["key"] for option in response.context["handoffs"][0]["options"]] == [
        "lmis",
    ]
    assert b'aria-label="Continue to DHIS as LMIS"' in response.content
    assert b'aria-label="Continue to DHIS as HMIS"' not in response.content


def test_overview_hides_dhis_after_wasa_is_revoked(eligible_hmis, client):
    approval = current_wasa(eligible_hmis["workspace"].product)
    approval.status = "revoked"
    approval.save(update_fields=["status"])
    client.force_login(eligible_hmis["applicant"])

    response = client.get(overview_url(eligible_hmis))

    assert response.status_code == 200
    assert not response.context["handoffs"]
    assert b'id="handoff-dhis-heading"' not in response.content
    assert handoff_url(eligible_hmis).encode() not in response.content


def test_post_creates_signed_redirect_from_trusted_product_data(
    eligible_hmis,
    client,
    monkeypatch,
):
    client.force_login(eligible_hmis["applicant"])
    monkeypatch.setattr(dhis.time, "time_ns", lambda: 1_789_000_000_123_999_999)
    product = eligible_hmis["workspace"].product
    outcome_count = product.outcomes.count()

    response = client.post(
        handoff_url(eligible_hmis),
        {
            "option": "hmis",
            "client_id": "forged-id",
            "email": "forged@example.test",
            "wasa_valid_until": "2099-01-01",
            "next": "https://example.test/steal",
        },
    )

    payload = claims(response)
    assert payload["client_id"] == str(product.pk)
    assert payload["email"] == eligible_hmis["applicant"].email
    assert payload["role"] == "sandbox"
    assert payload["generatedTime"] == "17890000001230"
    assert payload["intent_request"] == "HMIS"
    assert payload["integration_level"] == "M3"
    assert payload["wasa_valid_upto_date"][0]["expiryDate"] == (
        current_wasa(product).valid_until.isoformat()
    )
    assert response["Referrer-Policy"] == "no-referrer"
    assert "no-store" in response["Cache-Control"]
    assert "no-cache" in response["Cache-Control"]
    event = handoff_events(eligible_hmis).get()
    assert event.actor_id == eligible_hmis["applicant"].pk
    assert event.action == "DHIS handoff created"
    assert event.detail == {"handoff": "dhis", "option": "hmis", "result": "created"}
    assert product.outcomes.count() == outcome_count
    assert not any(
        message.level == SUCCESS for message in get_messages(response.wsgi_request)
    )
    assert parse_qs(urlsplit(response.url).query)["value"][0] not in json.dumps(
        event.detail,
    )


def test_each_click_forwards_again_with_a_new_token(eligible_hmis, client, monkeypatch):
    client.force_login(eligible_hmis["applicant"])
    clock = iter([1_789_000_000_123_000_000, 1_789_000_000_456_000_000])
    monkeypatch.setattr(dhis.time, "time_ns", lambda: next(clock))

    first = client.post(handoff_url(eligible_hmis), {"option": "hmis"})
    second = client.post(handoff_url(eligible_hmis), {"option": "hmis"})

    assert claims(first)["client_id"] == claims(second)["client_id"]
    assert first.url != second.url
    assert (
        handoff_events(eligible_hmis).filter(action="DHIS handoff created").count() == 2
    )


@pytest.mark.parametrize("actor_key", ["reviewer", "admin"])
def test_staff_cannot_initiate_or_see_handoff_actions(environment, client, actor_key):
    client.force_login(environment[actor_key])

    page = client.get(overview_url(environment), follow=True)
    response = client.post(handoff_url(environment), {"option": "hmis"})

    assert page.status_code == 200
    assert page.redirect_chain[0][0] == reverse(
        "experiences:product-detail",
        args=[environment["workspace"].reference],
    )
    assert "handoffs" not in page.context
    assert handoff_url(environment).encode() not in page.content
    assert response.status_code == 403
    assert not handoff_events(environment).exists()


def test_foreign_product_is_not_exposed(environment, client):
    client.force_login(environment["outsider"])

    response = client.post(handoff_url(environment), {"option": "hmis"})

    assert response.status_code == 404
    assert not handoff_events(environment).exists()


def test_anonymous_request_requires_login(environment, client):
    response = client.post(handoff_url(environment), {"option": "hmis"})

    assert response.status_code == 302
    assert response.url.startswith(reverse("account_login"))
    assert not handoff_events(environment).exists()


def test_handoff_rejects_get(environment, client):
    client.force_login(environment["applicant"])

    response = client.get(handoff_url(environment), {"option": "hmis"})

    assert response.status_code == 405
    assert not handoff_events(environment).exists()


def test_handoff_requires_csrf(environment):
    client = Client(enforce_csrf_checks=True)
    client.force_login(environment["applicant"])

    response = client.post(handoff_url(environment), {"option": "hmis"})

    assert response.status_code == 403
    assert not handoff_events(environment).exists()


def test_unknown_handoff_is_not_available(environment, client):
    client.force_login(environment["applicant"])

    response = client.post(handoff_url(environment, "unregistered"), {"option": "hmis"})

    assert response.status_code == 404
    assert not handoff_events(environment).exists()


@pytest.mark.parametrize(
    "option",
    ["", "phr", "<script>untrusted@example.test</script>"],
)
def test_unknown_option_is_blocked_without_recording_raw_input(
    eligible_hmis,
    client,
    option,
):
    client.force_login(eligible_hmis["applicant"])

    response = client.post(handoff_url(eligible_hmis), {"option": option})

    assert response.status_code == 302
    assert response.url == overview_url(eligible_hmis)
    event = handoff_events(eligible_hmis).get()
    assert event.action == "DHIS handoff blocked"
    assert event.detail == {"handoff": "dhis", "option": "", "result": "blocked"}


def test_unapproved_solution_cannot_be_posted_directly(eligible_hmis, client):
    client.force_login(eligible_hmis["applicant"])

    response = client.post(handoff_url(eligible_hmis), {"option": "lmis"})

    assert response.status_code == 302
    assert response.url == overview_url(eligible_hmis)
    assert handoff_events(eligible_hmis).get().detail == {
        "handoff": "dhis",
        "option": "lmis",
        "result": "blocked",
    }


@pytest.mark.parametrize(
    "change",
    ["wasa_expired", "wasa_revoked", "milestone_revoked", "solution_pending"],
)
def test_post_rechecks_changes_after_the_page_was_loaded(eligible_hmis, client, change):
    client.force_login(eligible_hmis["applicant"])
    product = eligible_hmis["workspace"].product
    page = client.get(overview_url(eligible_hmis))
    assert next(
        option
        for option in page.context["handoffs"][0]["options"]
        if option["key"] == "hmis"
    )["enabled"]
    if change == "solution_pending":
        change_solutions(eligible_hmis, ["lmis"])
    elif change == "milestone_revoked":
        product.outcomes.filter(
            outcome_type="milestone_approval",
            source_application__milestone__key="m2",
        ).update(status="revoked")
    else:
        approval = current_wasa(product)
        if change == "wasa_expired":
            approval.valid_until = timezone.localdate() - timedelta(days=1)
            approval.save(update_fields=["valid_until"])
        else:
            approval.status = "revoked"
            approval.save(update_fields=["status"])

    response = client.post(handoff_url(eligible_hmis), {"option": "hmis"})

    assert response.status_code == 302
    assert response.url == overview_url(eligible_hmis)
    assert handoff_events(eligible_hmis).get().action == "DHIS handoff blocked"


def test_post_uses_a_certificate_approved_after_the_page_loaded(eligible_hmis, client):
    client.force_login(eligible_hmis["applicant"])
    client.get(overview_url(eligible_hmis))
    renewal = decide(
        eligible_hmis,
        request_renewal(
            eligible_hmis,
            certificate=certificate_data(expires_in=90, audited_ago=1),
        ),
    )

    response = client.post(handoff_url(eligible_hmis), {"option": "hmis"})

    assert (
        claims(response)["wasa_valid_upto_date"][0]["expiryDate"]
        == (renewal.selected_submission.data["wasa_valid_until"])
    )


def test_missing_configuration_returns_a_safe_error(eligible_hmis, client, settings):
    client.force_login(eligible_hmis["applicant"])
    settings.ABDM_DHIS_AES_KEY = ""

    response = client.post(handoff_url(eligible_hmis), {"option": "hmis"})

    assert response.status_code == 302
    assert response.url == overview_url(eligible_hmis)
    messages = list(get_messages(response.wsgi_request))
    assert messages
    assert all(message.level == ERROR for message in messages)
    rendered = " ".join(str(message) for message in messages)
    assert "DHIS" in rendered
    assert CONFIG.signing_secret not in rendered
    assert CONFIG.encryption_iv not in rendered
    assert "ABDM_DHIS_AES_KEY" not in rendered
    event = handoff_events(eligible_hmis).get()
    assert event.action == "DHIS handoff blocked"
    assert event.detail == {"handoff": "dhis", "option": "hmis", "result": "blocked"}
