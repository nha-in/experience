from datetime import timedelta
from time import monotonic

from anymail.exceptions import AnymailConfigurationError
from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.mail import get_connection
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from ohc_experience.core.mail import GLOBAL_EMAIL_BACKEND
from ohc_experience.core.mail import apply_gateway_template
from ohc_experience.core.mail import gateway_template_id
from ohc_experience.core.mail import get_delivery_backend
from ohc_experience.core.mail.backends import GlobalEmailAPIError
from ohc_experience.organisations.models import MANAGER_ROLES

from .models import EventRegistration
from .models import Notification
from .models import ProductOutcome
from .models import ProductOutcomeStatus
from .permissions import visible_events
from .registry import get_program

NOTIFICATION_MAX_ATTEMPTS = 5
NOTIFICATION_BATCH_SIZE = 20
NOTIFICATION_BATCH_SECONDS = 30
# Three warnings before a dated outcome lapses, matching the windows integrators
# already know from the legacy portal, so a renewal can be arranged in time.
OUTCOME_REMINDER_DAYS = (30, 15, 7)
# NHA registered this warning as its own template, apart from the event notice
# every other queued message falls back to.
EXPIRY_TEMPLATE_KEY = "certificate_expiry"


class NotificationNotAcceptedError(RuntimeError):
    """The backend did not accept the single queued message."""


@shared_task
def deliver_notifications():
    """Submit due outbox rows, retaining uncertain gateway outcomes for review."""
    deadline = monotonic() + NOTIFICATION_BATCH_SECONDS
    for _ in range(NOTIFICATION_BATCH_SIZE):
        if monotonic() >= deadline:
            break
        with transaction.atomic():
            notification = (
                Notification.objects.select_for_update(skip_locked=True)
                .filter(
                    sent_at=None,
                    failed_at=None,
                    attempts__lt=NOTIFICATION_MAX_ATTEMPTS,
                    next_attempt_at__lte=timezone.now(),
                )
                .order_by("next_attempt_at", "pk")
                .first()
            )
            if not notification:
                break
            _deliver_notification(notification)


def _deliver_notification(notification):
    notification.attempts += 1
    backend = get_delivery_backend()
    try:
        connection = get_connection(backend=backend, fail_silently=False)
        message = EmailMessage(
            notification.subject,
            notification.body,
            notification.from_email or None,
            [notification.recipient],
            cc=notification.cc,
            connection=connection,
        )
        if backend == GLOBAL_EMAIL_BACKEND:
            if notification.template_id:
                message.template_id = notification.template_id
            else:
                apply_gateway_template(message, "notification")
                notification.template_id = message.template_id
            message.esp_extra = {
                "request_id": str(notification.request_id),
                "content_type": notification.content_type,
            }
        if message.send(fail_silently=False) != 1:
            raise NotificationNotAcceptedError  # noqa: TRY301 - use the persisted failure path.
    except AnymailConfigurationError:
        # Configuration must be fixed for the whole worker. Roll back this row
        # and stop the batch instead of exhausting otherwise valid queued mail.
        raise
    except Exception as error:  # noqa: BLE001 - mail backends raise backend-specific errors.
        _notification_failed(notification, backend, error)
    else:
        notification.sent_at = timezone.now()
        notification.last_error = ""
        message_id = getattr(
            getattr(message, "anymail_status", None),
            "message_id",
            None,
        )
        if isinstance(message_id, str):
            notification.provider_message_id = message_id[:255]
    notification.save()


def _notification_failed(notification, backend, error):
    notification.last_error = type(error).__name__[:255]
    retryable = backend != GLOBAL_EMAIL_BACKEND
    if isinstance(error, GlobalEmailAPIError):
        if error.reason_code in GlobalEmailAPIError.reason_codes:
            notification.last_error = error.reason_code
        retryable = error.retryable is True
    if isinstance(error, NotificationNotAcceptedError):
        retryable = False
    now = timezone.now()
    if retryable and notification.attempts < NOTIFICATION_MAX_ATTEMPTS:
        notification.next_attempt_at = now + timedelta(
            seconds=60 * 2 ** (notification.attempts - 1),
        )
    else:
        notification.failed_at = now


@shared_task
def remind_event_registrations():
    now = timezone.now()
    registrations = EventRegistration.objects.filter(
        reminder_sent=False,
        event__published_at__isnull=False,
        event__starts_at__gt=now,
        event__starts_at__lte=now + timedelta(hours=24),
    )
    for registration_id in registrations.values_list("pk", flat=True):
        with transaction.atomic():
            registration = (
                EventRegistration.objects.select_for_update()
                .select_related("event", "user")
                .get(pk=registration_id)
            )
            if (
                registration.reminder_sent
                or not visible_events(registration.user)
                .filter(pk=registration.event_id)
                .exists()
            ):
                continue
            starts_at = timezone.localtime(registration.event.starts_at)
            Notification.objects.create(
                recipient=registration.user.email,
                subject=(
                    f"{get_program().short_name}: reminder - {registration.event.title}"
                ),
                body=(
                    f"{registration.event.title}\n"
                    f"{starts_at:%d %b %Y, %H:%M %Z}\n"
                    f"{registration.event.join_url}"
                ),
            )
            registration.reminder_sent = True
            registration.save(update_fields=["reminder_sent"])


@shared_task
def remind_expiring_outcomes():
    """Warn an organisation before a dated product outcome lapses."""
    today = timezone.localdate()
    # A null expiry never satisfies the range, so undated outcomes stay out.
    due = ProductOutcome.objects.filter(
        status=ProductOutcomeStatus.ACTIVE,
        valid_until__gte=today,
        valid_until__lte=today + timedelta(days=max(OUTCOME_REMINDER_DAYS)),
    ).values_list("pk", flat=True)
    for outcome_id in list(due):
        with transaction.atomic():
            outcome = (
                ProductOutcome.objects.select_for_update()
                .select_related("product__organisation")
                .get(pk=outcome_id)
            )
            _remind_outcome(outcome, today)


def _superseded(outcome) -> bool:
    """A renewal issues a fresh outcome, so only the last one to lapse matters."""
    return ProductOutcome.objects.filter(
        product_id=outcome.product_id,
        outcome_type=outcome.outcome_type,
        status=ProductOutcomeStatus.ACTIVE,
        valid_until__gt=outcome.valid_until,
    ).exists()


def _outcome_recipients(organisation):
    """The owner arranges the renewal; the other managers are kept in the loop."""
    managers = organisation.memberships.filter(
        role__in=MANAGER_ROLES,
    ).select_related("user")
    emails = {member.user.email for member in managers if member.user.email}
    owner = organisation.owner
    primary = owner.email if owner and owner.email else next(iter(sorted(emails)), "")
    return primary, sorted(emails - {primary})


def _outcome_url(product) -> str:
    """The certification page carries the certificate and the renewal action."""
    base = settings.SITE_BASE_URL.rstrip("/")
    workspace = getattr(product, "workspace", None)
    if not workspace:
        return base
    path = reverse("experiences:product-certification", args=[workspace.reference])
    return f"{base}{path}"


def _remind_outcome(outcome, today) -> None:
    days = (outcome.valid_until - today).days
    sent = outcome.metadata.get("expiry_reminders") or []
    # Passing several windows at once, after a quiet worker or a late approval,
    # owes one mail stating the real days left, not one mail per window missed.
    windows = [day for day in OUTCOME_REMINDER_DAYS if days <= day and day not in sent]
    if not windows or _superseded(outcome):
        return
    recipient, cc = _outcome_recipients(outcome.product.organisation)
    if not recipient:
        return
    context = {
        "brand": get_program().short_name,
        "outcome": outcome,
        "product": outcome.product,
        "days": days,
        "url": _outcome_url(outcome.product),
    }
    Notification.objects.create(
        recipient=recipient,
        cc=cc,
        subject=render_to_string(
            "experiences/email/outcome_expiry_subject.txt",
            context,
        ).strip(),
        body=render_to_string("experiences/email/outcome_expiry_body.txt", context),
        template_id=gateway_template_id(EXPIRY_TEMPLATE_KEY),
    )
    outcome.metadata["expiry_reminders"] = sorted({*sent, *windows}, reverse=True)
    outcome.save(update_fields=["metadata", "updated_at"])
