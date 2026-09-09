from unittest.mock import Mock

import pytest
from allauth.account.adapter import DefaultAccountAdapter
from anymail.exceptions import AnymailConfigurationError
from anymail.exceptions import AnymailUnsupportedFeature
from django.core import mail
from django.db import IntegrityError
from django.db import transaction

from ohc_experience.core.mail import GLOBAL_EMAIL_BACKEND
from ohc_experience.core.mail import QUEUED_GLOBAL_EMAIL_BACKEND
from ohc_experience.core.mail import apply_gateway_template
from ohc_experience.core.mail import get_delivery_backend
from ohc_experience.core.mail.backends import GlobalEmailBackend
from ohc_experience.experiences.models import Notification
from ohc_experience.experiences.tasks import deliver_notifications
from ohc_experience.organisations.tests.factories import InvitationFactory
from ohc_experience.organisations.views import send_invitation_email
from ohc_experience.users.adapters import AccountAdapter

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def gateway_settings(settings, monkeypatch):
    settings.EMAIL_BACKEND = QUEUED_GLOBAL_EMAIL_BACKEND
    settings.ANYMAIL = {
        "GLOBAL_EMAIL_API_URL": "https://gateway.invalid/internal/v3/notification/email/send",
        "GLOBAL_EMAIL_TEMPLATE_ID": "100001",
    }
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {}
    forbidden = Mock(side_effect=AssertionError("Unexpected network request"))
    monkeypatch.setattr("requests.Session.request", forbidden)
    return forbidden


def message(**kwargs):
    return mail.EmailMessage(
        subject="Application update",
        body="Please review the update in your portal.",
        to=["applicant@example.org"],
        **kwargs,
    )


def test_mail_is_enqueued_without_contacting_gateway(gateway_settings):
    email = message(cc=["reviewer@example.org"])
    email.template_id = "74002"
    email.esp_extra = {"content_type": "info"}

    assert email.send() == 1
    row = Notification.objects.get()
    assert row.recipient == "applicant@example.org"
    assert row.cc == ["reviewer@example.org"]
    assert row.body == email.body
    assert row.subject == email.subject
    assert row.template_id == "74002"
    assert row.content_type == "info"
    assert row.sent_at is None
    assert row.attempts == 0
    assert email.anymail_status.status == {"queued"}
    assert email.anymail_status.message_id == str(row.request_id)
    gateway_settings.assert_not_called()


def test_queue_uses_plaintext_from_multipart_email():
    email = mail.EmailMultiAlternatives(
        "Verify email",
        "Use this verification link.",
        to=["applicant@example.org"],
    )
    email.attach_alternative("<p>Use this verification link.</p>", "text/html")
    email.send()
    assert Notification.objects.get().body == "Use this verification link."


def test_queue_rolls_back_with_account_transaction():
    def rolled_back_request():
        with transaction.atomic():
            message().send()
            error = "rollback"
            raise ValueError(error)

    with pytest.raises(ValueError, match="rollback"):
        rolled_back_request()
    assert not Notification.objects.exists()


def test_all_messages_are_validated_before_batch_enqueue():
    unsupported = message()
    unsupported.bcc = ["hidden@example.org"]
    with pytest.raises(AnymailUnsupportedFeature):
        mail.get_connection().send_messages([message(), unsupported])
    assert not Notification.objects.exists()


def test_silent_batch_skips_invalid_messages_and_enqueues_valid_messages():
    before = message()
    unsupported = message()
    unsupported.bcc = ["hidden@example.org"]
    after = message()

    sent = mail.get_connection(fail_silently=True).send_messages(
        [before, unsupported, after],
    )

    assert sent == 2  # noqa: PLR2004
    assert Notification.objects.count() == 2  # noqa: PLR2004
    assert before.anymail_status.status == {"queued"}
    assert after.anymail_status.status == {"queued"}


@pytest.mark.parametrize("fail_silently", [False, True])
def test_database_failure_preserves_callers_transaction(fail_silently):
    message().send()
    existing = Notification.objects.get()
    duplicate = message()
    duplicate.esp_extra = {"request_id": str(existing.request_id)}
    connection = mail.get_connection(fail_silently=fail_silently)

    with transaction.atomic():
        if fail_silently:
            assert connection.send_messages([message(), duplicate]) == 0
        else:
            with pytest.raises(IntegrityError):
                connection.send_messages([message(), duplicate])
        assert Notification.objects.count() == 1
        assert message().send() == 1

    assert Notification.objects.count() == 2  # noqa: PLR2004


@pytest.mark.parametrize("fail_silently", [False, True])
def test_missing_template_does_not_enqueue(settings, fail_silently):
    settings.ANYMAIL = {"GLOBAL_EMAIL_API_URL": "https://gateway.invalid/email/send"}
    with pytest.raises(AnymailConfigurationError):
        message().send(fail_silently=fail_silently)
    assert not Notification.objects.exists()


@pytest.mark.parametrize(
    "purpose",
    ["organisation_invitation", "account/email/password_reset_key"],
)
def test_unmapped_specific_purpose_cannot_use_workflow_template(settings, purpose):
    settings.ANYMAIL = {"GLOBAL_EMAIL_API_URL": "https://gateway.invalid/email/send"}
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {"notification": "74002"}
    email = message()
    with pytest.raises(AnymailConfigurationError):
        apply_gateway_template(email, purpose)
    assert not Notification.objects.exists()


@pytest.mark.parametrize("mapping", [None, [], "100001"])
def test_invalid_template_mapping_fails_cleanly(settings, mapping):
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = mapping
    with pytest.raises(AnymailConfigurationError, match="GLOBAL_EMAIL_TEMPLATE_IDS"):
        message().send()
    assert not Notification.objects.exists()


@pytest.mark.parametrize("template_id", [None, "", " ", " bad-id ", 100001, True])
def test_invalid_purpose_id_never_falls_back(settings, template_id):
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {"organisation_invitation": template_id}
    with pytest.raises(AnymailConfigurationError, match="organisation_invitation"):
        apply_gateway_template(message(), "organisation_invitation")
    assert not Notification.objects.exists()


@pytest.mark.parametrize(
    ("defaults", "gateway_defaults", "expected"),
    [
        ({"template_id": "general-default"}, None, "general-default"),
        (
            {"template_id": "general-default"},
            {"template_id": "gateway-default"},
            "gateway-default",
        ),
        ({"template_id": "general-default"}, {"template_id": None}, "100001"),
    ],
)
def test_queue_matches_anymail_template_defaults(
    settings,
    defaults,
    gateway_defaults,
    expected,
):
    settings.ANYMAIL["SEND_DEFAULTS"] = defaults
    settings.ANYMAIL["GLOBAL_EMAIL_SEND_DEFAULTS"] = gateway_defaults
    transport = GlobalEmailBackend()
    direct_payload = transport.build_message_payload(message(), transport.send_defaults)
    email = message()
    email.send()
    assert Notification.objects.get().template_id == direct_payload.data["templateId"]
    assert direct_payload.data["templateId"] == expected


def test_explicit_none_suppresses_anymail_send_default(settings):
    settings.ANYMAIL["SEND_DEFAULTS"] = {"template_id": "general-default"}
    email = message()
    email.template_id = None
    email.send()
    assert Notification.objects.get().template_id == "100001"


def test_purpose_mapping_overrides_anymail_send_default(settings):
    settings.ANYMAIL["GLOBAL_EMAIL_SEND_DEFAULTS"] = {"template_id": "gateway-default"}
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {"notification": "workflow-template"}
    message().send()
    assert Notification.objects.get().template_id == "workflow-template"


def test_queue_fail_silently_returns_zero_for_invalid_message():
    email = message()
    email.bcc = ["hidden@example.org"]
    assert email.send(fail_silently=True) == 0
    assert not Notification.objects.exists()


def test_template_mapping_preserves_explicit_id_and_other_backends(settings):
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {"organisation_invitation": "74050"}
    email = message()
    apply_gateway_template(email, "organisation_invitation")
    assert email.template_id == "74050"
    email.template_id = "74051"
    apply_gateway_template(email, "organisation_invitation")
    assert email.template_id == "74051"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    email = message()
    apply_gateway_template(email, "organisation_invitation")
    assert not hasattr(email, "template_id")


def test_allauth_uses_prefix_mapping_before_queueing(settings, monkeypatch):
    key = "account/email/password_reset_key"
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {key: "74060"}
    render = Mock(return_value=message())
    monkeypatch.setattr(DefaultAccountAdapter, "render_mail", render)
    email = AccountAdapter().render_mail(key, "applicant@example.org", {})
    email.send()
    assert Notification.objects.get().template_id == "74060"


def test_invitation_uses_dedicated_template_and_is_queued(settings, rf):
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {"organisation_invitation": "74050"}
    invitation = InvitationFactory()
    send_invitation_email(rf.get("/"), invitation)
    row = Notification.objects.get()
    assert row.template_id == "74050"
    assert row.recipient == invitation.email
    assert invitation.token in row.body
    assert row.sent_at is None


def test_worker_delivers_queued_mail_without_enqueuing_again(monkeypatch):
    email = message(cc=["reviewer@example.org"])
    email.send()
    row = Notification.objects.get()
    calls = []

    def respond(_session, **kwargs):
        import json  # noqa: PLC0415

        data = json.loads(kwargs["data"])
        calls.append(data)
        return Mock(
            status_code=200,
            json=Mock(
                return_value={
                    "requestId": data["requestId"],
                    "templateId": int(data["templateId"]),
                    "receiver": data["receiver"],
                    "status": "SUCCESS",
                    "gatewayTxnid": "gateway-accepted-123",
                },
            ),
        )

    monkeypatch.setattr("requests.Session.request", respond)
    assert get_delivery_backend() == GLOBAL_EMAIL_BACKEND
    deliver_notifications()
    row.refresh_from_db()
    assert Notification.objects.count() == 1
    assert row.sent_at is not None
    assert row.provider_message_id == "gateway-accepted-123"
    assert calls[0]["requestId"] == str(row.request_id)
    assert calls[0]["ccRecipients"] == ["reviewer@example.org"]
    deliver_notifications()
    assert len(calls) == 1
