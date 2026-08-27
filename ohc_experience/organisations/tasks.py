from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import timezone

from .care_plugin import CarePluginClient
from .care_plugin import CarePluginError
from .models import Sandbox

logger = logging.getLogger(__name__)

SANDBOX_EMAIL_CC = ["support@ohc.network"]


def _fail(sandbox: Sandbox, message: str) -> None:
    sandbox.error = message
    sandbox.status = Sandbox.Status.FAILED
    sandbox.save(update_fields=["error", "status", "modified_at"])


def send_sandbox_credentials_email(sandbox: Sandbox) -> None:
    """Email the ready sandbox's credentials to whoever requested it."""
    requester = sandbox.requested_by
    if requester is None or not requester.email or not sandbox.credentials:
        return
    context = {
        "sandbox": sandbox,
        "cred": sandbox.primary_credential,
        "sandbox_frontend_url": settings.CARE_SANDBOX_FRONTEND_URL,
    }
    subject = render_to_string(
        "organisations/email/sandbox_credentials_subject.txt",
        context,
    ).strip()
    body = render_to_string("organisations/email/sandbox_credentials_body.txt", context)
    EmailMessage(
        subject=subject,
        body=body,
        to=[requester.email],
        cc=SANDBOX_EMAIL_CC,
    ).send(fail_silently=False)


@shared_task
def provision_sandbox(sandbox_id: int) -> None:
    sandbox = Sandbox.objects.get(pk=sandbox_id)
    sandbox.status = Sandbox.Status.PROVISIONING
    sandbox.error = ""
    sandbox.save(update_fields=["status", "error", "modified_at"])

    try:
        job = CarePluginClient().create_sandbox(
            sandbox.facility_name,
            is_facility_empty=sandbox.is_facility_empty,
        )
    except CarePluginError as exc:
        _fail(sandbox, str(exc))
        return

    job_id = job.get("id")
    if not job_id:
        _fail(sandbox, "Care plugin did not return a job id.")
        return

    sandbox.job_id = job_id
    sandbox.save(update_fields=["job_id", "modified_at"])
    poll_sandbox.apply_async(
        (sandbox_id, 0),
        countdown=settings.CARE_SANDBOX_POLL_INTERVAL,
    )


@shared_task
def poll_sandbox(sandbox_id: int, attempt: int) -> None:
    sandbox = Sandbox.objects.get(pk=sandbox_id)
    if not sandbox.job_id or sandbox.status != Sandbox.Status.PROVISIONING:
        return

    try:
        payload = CarePluginClient().get_sandbox(sandbox.job_id)
    except CarePluginError as exc:
        _fail(sandbox, str(exc))
        return

    state = payload.get("status")
    if state == "completed":
        sandbox.result = payload.get("result") or {}
        sandbox.status = Sandbox.Status.READY
        sandbox.provisioned_at = timezone.now()
        sandbox.save(
            update_fields=["result", "status", "provisioned_at", "modified_at"],
        )
        try:
            send_sandbox_credentials_email(sandbox)
        except Exception:
            # The sandbox is already saved as ready; a mail failure must not
            # crash the task or misreport its status.
            logger.exception("Failed to email sandbox %s credentials", sandbox.pk)
        return
    if state == "failed":
        _fail(sandbox, payload.get("error") or "Sandbox creation failed.")
        return

    if attempt + 1 >= settings.CARE_SANDBOX_POLL_ATTEMPTS:
        _fail(sandbox, "Timed out waiting for the sandbox to be provisioned.")
        return
    poll_sandbox.apply_async(
        (sandbox_id, attempt + 1),
        countdown=settings.CARE_SANDBOX_POLL_INTERVAL,
    )
