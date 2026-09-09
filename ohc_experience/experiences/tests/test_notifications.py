# ruff: noqa: PLR2004
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from anymail.exceptions import AnymailConfigurationError
from django.contrib import admin
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from ohc_experience.core.mail import GLOBAL_EMAIL_BACKEND
from ohc_experience.core.mail import QUEUED_GLOBAL_EMAIL_BACKEND
from ohc_experience.core.mail.backends import GlobalEmailAPIError
from ohc_experience.experiences import tasks
from ohc_experience.experiences.models import Notification

pytestmark = pytest.mark.django_db


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=timezone.now())
    monkeypatch.setattr(tasks.timezone, "now", lambda: clock.now)
    return clock


@pytest.fixture
def mail_backend(monkeypatch):
    backend = SimpleNamespace(send_messages=Mock(return_value=1))
    monkeypatch.setattr(tasks, "get_connection", Mock(return_value=backend))
    return backend


@pytest.fixture
def notification(clock):
    return Notification.objects.create(
        recipient="applicant@example.test",
        subject="Application update",
        body="Your application is ready for review.",
        next_attempt_at=clock.now,
    )


def configure_gateway(settings):
    settings.EMAIL_BACKEND = QUEUED_GLOBAL_EMAIL_BACKEND
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {"notification": "1001173952060000001"}


def test_accepted_gateway_message_preserves_envelope_and_metadata(
    settings,
    notification,
    mail_backend,
    clock,
):
    configure_gateway(settings)
    notification.from_email = "Sandbox <sandbox@example.test>"
    notification.cc = ["reviewer@example.test"]
    notification.content_type = "otp"
    notification.save()

    def accept(messages):
        messages[0].anymail_status = SimpleNamespace(message_id="gateway-123")
        return 1

    mail_backend.send_messages.side_effect = accept
    tasks.deliver_notifications()

    message = mail_backend.send_messages.call_args.args[0][0]
    notification.refresh_from_db()
    assert message.to == [notification.recipient]
    assert message.from_email == notification.from_email
    assert message.cc == notification.cc
    assert message.body == notification.body
    assert message.template_id == notification.template_id == "1001173952060000001"
    assert message.esp_extra == {
        "request_id": str(notification.request_id),
        "content_type": "otp",
    }
    assert notification.sent_at == clock.now
    assert notification.provider_message_id == "gateway-123"
    assert notification.attempts == 1
    assert notification.failed_at is None
    assert notification.last_error == ""
    tasks.get_connection.assert_called_once_with(
        backend=GLOBAL_EMAIL_BACKEND,
        fail_silently=False,
    )


def test_generic_backend_does_not_receive_gateway_attributes(
    notification,
    mail_backend,
    settings,
):
    notification.template_id = "1001173952060000001"
    notification.save()
    tasks.deliver_notifications()
    message = mail_backend.send_messages.call_args.args[0][0]
    assert not hasattr(message, "template_id")
    assert not hasattr(message, "esp_extra")
    tasks.get_connection.assert_called_once_with(
        backend=settings.EMAIL_BACKEND,
        fail_silently=False,
    )
    notification.refresh_from_db()
    assert notification.sent_at is not None


@pytest.mark.parametrize("state", ["future", "accepted", "failed", "exhausted"])
def test_worker_skips_ineligible_rows(notification, mail_backend, clock, state):
    if state == "future":
        notification.next_attempt_at = clock.now + timedelta(minutes=1)
    elif state == "accepted":
        notification.sent_at = clock.now
    elif state == "failed":
        notification.failed_at = clock.now
    else:
        notification.attempts = 5
    notification.save()
    tasks.deliver_notifications()
    mail_backend.send_messages.assert_not_called()


def test_repeated_worker_does_not_resubmit_accepted_message(notification, mail_backend):
    tasks.deliver_notifications()
    tasks.deliver_notifications()
    mail_backend.send_messages.assert_called_once()
    notification.refresh_from_db()
    assert notification.attempts == 1


def test_generic_failures_back_off_and_stop_after_five_attempts(
    notification,
    mail_backend,
    clock,
):
    mail_backend.send_messages.side_effect = RuntimeError("secret OTP 123456")
    for attempt, delay in enumerate([60, 120, 240, 480, None], start=1):
        tasks.deliver_notifications()
        notification.refresh_from_db()
        assert notification.attempts == attempt
        assert notification.sent_at is None
        assert notification.last_error == "RuntimeError"
        if delay is None:
            assert notification.failed_at == clock.now
        else:
            assert notification.failed_at is None
            assert notification.next_attempt_at == clock.now + timedelta(seconds=delay)
        tasks.deliver_notifications()
        assert mail_backend.send_messages.call_count == attempt
        if delay is not None:
            clock.now = notification.next_attempt_at


def test_safe_gateway_retries_preserve_request_and_template_ids(
    notification,
    mail_backend,
    clock,
    settings,
):
    configure_gateway(settings)
    mail_backend.send_messages.side_effect = [
        GlobalEmailAPIError("connection_timeout", retryable=True),
        1,
    ]
    tasks.deliver_notifications()
    notification.refresh_from_db()
    assert notification.attempts == 1
    assert notification.failed_at is None
    assert notification.last_error == "connection_timeout"
    assert notification.next_attempt_at == clock.now + timedelta(seconds=60)
    tasks.deliver_notifications()
    assert mail_backend.send_messages.call_count == 1

    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {"notification": "1001173952060000002"}
    clock.now = notification.next_attempt_at
    tasks.deliver_notifications()
    messages = [call.args[0][0] for call in mail_backend.send_messages.call_args_list]
    assert [message.template_id for message in messages] == ["1001173952060000001"] * 2
    assert [message.esp_extra["request_id"] for message in messages] == [
        str(notification.request_id),
    ] * 2
    notification.refresh_from_db()
    assert notification.sent_at == clock.now
    assert notification.last_error == ""
    assert notification.attempts == 2


@pytest.mark.parametrize(
    "failure",
    [
        (RuntimeError("secret OTP 123456"), "RuntimeError"),
        (GlobalEmailAPIError("timeout"), "timeout"),
        (GlobalEmailAPIError("gateway_rejected"), "gateway_rejected"),
        (GlobalEmailAPIError("http_error", status_code=503), "http_error"),
        (GlobalEmailAPIError("sensitive response body"), "GlobalEmailAPIError"),
    ],
)
def test_uncertain_or_rejected_gateway_outcomes_are_terminal(
    settings,
    notification,
    mail_backend,
    clock,
    failure,
):
    error, safe_error = failure
    configure_gateway(settings)
    mail_backend.send_messages.side_effect = error
    tasks.deliver_notifications()
    notification.refresh_from_db()
    assert notification.failed_at == clock.now
    assert notification.sent_at is None
    assert notification.last_error == safe_error
    tasks.deliver_notifications()
    mail_backend.send_messages.assert_called_once()


@pytest.mark.parametrize("gateway", [True, False])
def test_zero_accepted_messages_is_terminal(
    settings,
    notification,
    mail_backend,
    clock,
    gateway,
):
    if gateway:
        configure_gateway(settings)
    mail_backend.send_messages.return_value = 0
    tasks.deliver_notifications()
    notification.refresh_from_db()
    assert notification.sent_at is None
    assert notification.failed_at == clock.now
    assert notification.last_error == "NotificationNotAcceptedError"
    tasks.deliver_notifications()
    mail_backend.send_messages.assert_called_once()


def test_gateway_configuration_error_leaves_pending_mail_unchanged(
    settings,
    notification,
    monkeypatch,
):
    configure_gateway(settings)
    monkeypatch.setattr(
        tasks,
        "get_connection",
        Mock(side_effect=AnymailConfigurationError("sensitive configuration")),
    )
    with pytest.raises(AnymailConfigurationError):
        tasks.deliver_notifications()
    notification.refresh_from_db()
    assert notification.attempts == 0
    assert notification.failed_at is None
    assert notification.last_error == ""
    assert notification.template_id == ""


def test_template_configuration_error_stops_before_sending(
    settings,
    notification,
    mail_backend,
):
    configure_gateway(settings)
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {}
    settings.ANYMAIL = {}
    with pytest.raises(AnymailConfigurationError):
        tasks.deliver_notifications()
    mail_backend.send_messages.assert_not_called()
    notification.refresh_from_db()
    assert notification.attempts == 0
    assert notification.failed_at is None


def test_worker_stops_after_time_budget(notification, mail_backend, monkeypatch, clock):
    second = Notification.objects.create(
        recipient="second@example.test",
        subject="Second message",
        body="Message body.",
        next_attempt_at=clock.now,
    )
    monkeypatch.setattr(tasks, "monotonic", Mock(side_effect=[0, 0, 31]))
    tasks.deliver_notifications()
    mail_backend.send_messages.assert_called_once()
    notification.refresh_from_db()
    second.refresh_from_db()
    assert notification.sent_at is not None
    assert second.attempts == 0


def test_worker_bounds_batch_size(mail_backend, clock):
    Notification.objects.bulk_create(
        [
            Notification(
                recipient=f"recipient{index}@example.test",
                subject="Message",
                body="Message body.",
                next_attempt_at=clock.now,
            )
            for index in range(21)
        ],
    )
    tasks.deliver_notifications()
    assert mail_backend.send_messages.call_count == 20
    assert Notification.objects.filter(sent_at__isnull=True, attempts=0).count() == 1


def test_admin_identifies_exhausted_historical_rows_without_resending(notification):
    notification.attempts = 5
    model_admin = admin.site.get_model_admin(Notification)
    assert model_admin.delivery_status(notification) == "Needs review"
    assert notification.failed_at is None


def test_migration_assigns_distinct_ids_and_preserves_existing_delivery_state():
    previous = [("experiences", "0009_merge_agency_and_solution_type")]
    target = [("experiences", "0010_notification_delivery")]
    executor = MigrationExecutor(connection)
    executor.migrate(previous)
    try:
        previous_apps = executor.loader.project_state(previous).apps
        old_notification = previous_apps.get_model("experiences", "Notification")
        accepted_at = timezone.now()
        accepted = old_notification.objects.create(
            recipient="accepted@example.test",
            subject="Already accepted",
            body="Original message.",
            attempts=2,
            sent_at=accepted_at,
        )
        failed = old_notification.objects.create(
            recipient="failed@example.test",
            subject="Exhausted message",
            body="Original failed message.",
            attempts=5,
            last_error="RuntimeError",
        )
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        accepted = Notification.objects.get(pk=accepted.pk)
        failed = Notification.objects.get(pk=failed.pk)
        assert accepted.request_id
        assert failed.request_id
        assert accepted.request_id != failed.request_id
        assert accepted.sent_at == accepted_at
        assert accepted.attempts == 2
        assert accepted.body == "Original message."
        assert failed.sent_at is None
        assert failed.attempts == 5
        assert failed.last_error == "RuntimeError"
        assert failed.failed_at is None
    finally:
        MigrationExecutor(connection).migrate(target)
