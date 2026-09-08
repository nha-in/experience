from datetime import timedelta

from celery import shared_task
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .credentials import check_callback
from .models import EventRegistration
from .models import Notification
from .models import SandboxCredential


@shared_task
def monitor_callbacks():
    for credential in SandboxCredential.objects.filter(
        status="active",
        product__organisation__verification_status="verified",
    ).exclude(callback_url=""):
        check_callback(credential)


@shared_task
def deliver_notifications():
    for _ in range(100):
        with transaction.atomic():
            notification = (
                Notification.objects.select_for_update(skip_locked=True)
                .filter(sent_at=None, attempts__lt=5)
                .order_by("pk")
                .first()
            )
            if not notification:
                break
            notification.attempts += 1
            try:
                send_mail(
                    notification.subject,
                    notification.body,
                    None,
                    [notification.recipient],
                    fail_silently=False,
                )
            except Exception as error:  # noqa: BLE001 - mail backends raise backend-specific errors.
                notification.last_error = type(error).__name__
            else:
                notification.sent_at = timezone.now()
                notification.last_error = ""
            notification.save()


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
            if registration.reminder_sent:
                continue
            starts_at = timezone.localtime(registration.event.starts_at)
            Notification.objects.create(
                recipient=registration.user.email,
                subject=f"ABDM: reminder - {registration.event.title}",
                body=(
                    f"{registration.event.title}\n"
                    f"{starts_at:%d %b %Y, %H:%M %Z}\n"
                    f"{registration.event.join_url}"
                ),
            )
            registration.reminder_sent = True
            registration.save(update_fields=["reminder_sent"])
