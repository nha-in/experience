"""Store gateway mail in the same durable outbox as workflow notifications."""

from anymail.exceptions import AnymailError
from anymail.message import AnymailRecipientStatus
from anymail.message import AnymailStatus
from django.core.mail.backends.base import BaseEmailBackend
from django.db import DatabaseError
from django.db import transaction

from ohc_experience.core.mail import apply_gateway_template
from ohc_experience.core.mail.backends import GlobalEmailBackend


class QueuedGlobalEmailBackend(BaseEmailBackend):
    """Validate locally and enqueue; only the Celery worker contacts the gateway."""

    def send_messages(self, email_messages):
        from ohc_experience.experiences.models import Notification  # noqa: PLC0415

        if not email_messages:
            return 0
        pending = []
        transport = GlobalEmailBackend()
        for message in email_messages:
            if not message.recipients():
                continue
            try:
                apply_gateway_template(message, "notification")
                payload = transport.build_message_payload(
                    message,
                    transport.send_defaults,
                )
            except AnymailError:
                if not self.fail_silently:
                    raise
                continue
            data = payload.data
            pending.append(
                (
                    message,
                    Notification(
                        request_id=data["requestId"],
                        recipient=data["receiver"],
                        cc=data["ccRecipients"] or [],
                        from_email=message.from_email or "",
                        subject=data["subject"],
                        body=data["content"],
                        template_id=data["templateId"],
                        content_type=data["contentType"],
                    ),
                    payload.all_recipients,
                ),
            )

        if not pending:
            return 0
        # Use a savepoint so a handled database failure does not invalidate the
        # caller's transaction. Successful rows still roll back with the caller.
        try:
            with transaction.atomic():
                Notification.objects.bulk_create([row for _, row, _ in pending])
        except DatabaseError:
            if not self.fail_silently:
                raise
            return 0
        for message, row, recipients in pending:
            message.anymail_status = AnymailStatus()
            message.anymail_status.set_recipient_status(
                {
                    recipient: AnymailRecipientStatus(str(row.request_id), "queued")
                    for recipient in recipients
                },
            )
        return len(pending)
