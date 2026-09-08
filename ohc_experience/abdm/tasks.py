"""Scheduled work: the callback monitor (every 15 minutes, see settings)."""

from __future__ import annotations

import logging

from celery import shared_task

from .models import Credential
from .services import check_callback

logger = logging.getLogger(__name__)


@shared_task
def run_callback_checks() -> int:
    """Probe every active credential's callback URL. Returns how many ran."""
    count = 0
    credentials = Credential.objects.filter(
        revoked_on__isnull=True,
    ).exclude(callback_url="")
    for credential in credentials.select_related("product__organisation"):
        try:
            check_callback(product=credential.product)
        except Exception:
            logger.exception("Callback check failed for %s", credential.client_id)
            continue
        count += 1
    return count
