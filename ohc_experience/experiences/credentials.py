import logging
import time
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.db import transaction
from django.utils import timezone

from ohc_experience.integrations.credentials import rotate_client
from ohc_experience.integrations.services import start_bridge_sync
from ohc_experience.integrations.services import start_deprovisioning

from .models import ProductCredential
from .permissions import require_integrator
from .secrets import cipher
from .workflows import audit

logger = logging.getLogger(__name__)


def rate_limit(actor, operation, limit=5):
    key = f"experiences:{operation}:{actor.pk}:{int(time.time()) // 60}"
    if cache.add(key, 1, timeout=65):
        return
    if cache.incr(key) > limit:
        msg = "Too many requests. Wait a minute and try again."
        raise ValidationError(msg)


def _locked_credential(credential):
    """This row only. Locking the organisation too would make one product's
    credential operation block every other write against that organisation."""
    return ProductCredential.objects.select_for_update().get(pk=credential.pk)


@transaction.atomic
def reveal(credential, actor):
    credential = _locked_credential(credential)
    require_integrator(actor, credential.product.organisation)
    rate_limit(actor, "reveal")
    if credential.status != "active":
        msg = "These credentials are no longer active."
        raise ValidationError(msg)
    audit(actor=actor, action="Client secret revealed", product=credential.product)
    return cipher().decrypt(credential.encrypted_secret.encode()).decode()


def rotate(credential, actor):
    """Rotate at the gateway, then store what it minted.

    The two cannot be made atomic across systems, so the write is kept as small
    as possible and a failure to persist is reported rather than swallowed: the
    integrator's stored secret is dead at that point and only they can act.
    """
    require_integrator(actor, credential.product.organisation)
    if credential.status != "active":
        msg = "These credentials are no longer active."
        raise ValidationError(msg)
    rate_limit(actor, "rotate", limit=2)

    secret = rotate_client(credential.product)

    try:
        with transaction.atomic():
            current = _locked_credential(credential)
            current.encrypted_secret = cipher().encrypt(secret.encode()).decode()
            current.issued_at = timezone.now()
            definition = current.product.workspace.definition.sandbox_credentials
            current.rotation_due = timezone.now() + timedelta(
                days=definition.rotation_days,
            )
            current.save(
                update_fields=["encrypted_secret", "issued_at", "rotation_due"],
            )
            audit(
                actor=actor,
                action="Integration credentials rotated",
                product=current.product,
            )
    except DatabaseError:
        logger.exception("storing the rotated secret for %s failed", credential.pk)
        msg = (
            "The gateway issued a new secret but it could not be saved. "
            "Rotate again to get a working one."
        )
        raise ValidationError(msg) from None


@transaction.atomic
def revoke(credential, actor):
    """Switch the integrator off in all three systems, then mark the row."""
    credential = _locked_credential(credential)
    require_integrator(actor, credential.product.organisation)
    if credential.status != "active":
        return
    start_deprovisioning(credential.product)
    credential.status = "revoked"
    credential.encrypted_secret = ""
    credential.save(update_fields=["status", "encrypted_secret"])
    audit(
        actor=actor,
        action="Integration credentials revoked",
        product=credential.product,
    )


@transaction.atomic
def save_callback_url(credential, actor, url):
    require_integrator(actor, credential.product.organisation)
    credential = ProductCredential.objects.select_for_update().get(pk=credential.pk)
    previous = credential.callback_url
    credential.callback_url = url
    credential.save(update_fields=["callback_url"])
    audit(
        actor=actor,
        action="Callback URL updated",
        product=credential.product,
        detail={"before": previous, "after": url},
    )
    # On every save, not only a change: re-saving is how a failure is retried.
    if url:
        start_bridge_sync(credential.product)


def retry_bridge(credential, actor):
    require_integrator(actor, credential.product.organisation)
    rate_limit(actor, "register", limit=2)
    if not credential.callback_url:
        msg = "Save a callback URL first."
        raise ValidationError(msg)
    start_bridge_sync(credential.product)
