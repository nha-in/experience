import http.client
import ipaddress
import logging
import socket
import ssl
import time
from datetime import timedelta
from http import HTTPStatus
from urllib.parse import urlsplit

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
from .workflows import notify_integrators

logger = logging.getLogger(__name__)

CALLBACK_FAILURE_ALERT_THRESHOLD = 3


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
    if previous != url:
        credential.last_checked_at = None
        credential.last_status = None
        credential.last_latency_ms = None
        credential.last_error = ""
        credential.consecutive_failures = 0
    credential.save()
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


def public_callback_target(url):
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.fragment
        or parts.port not in {None, 443}
    ):
        msg = "Use a public HTTPS endpoint on port 443 without credentials."
        raise ValidationError(
            msg,
        )
    addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(
        not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses
    ):
        msg = "Private, loopback and reserved callback addresses are not permitted."
        raise ValidationError(
            msg,
        )
    return parts, addresses[0][4][0]


def check_callback(credential, actor=None):
    if actor:
        require_integrator(actor, credential.product.organisation)
        rate_limit(actor, "callback")
    url = credential.callback_url
    if not url:
        msg = "Save a callback URL first."
        raise ValidationError(msg)
    started = time.monotonic()
    status, error = None, ""
    try:
        parts, address = public_callback_target(url)
        # Connect to the validated IP, retaining hostname verification and SNI.
        # No redirects are followed, so DNS rebinding cannot target internal hosts.
        context = ssl.create_default_context()
        with socket.create_connection((address, 443), timeout=5) as raw:  # noqa: SIM117
            with context.wrap_socket(raw, server_hostname=parts.hostname) as secured:
                connection = http.client.HTTPSConnection(parts.hostname, timeout=5)
                connection.sock = secured
                try:
                    path = (parts.path or "/") + (
                        f"?{parts.query}" if parts.query else ""
                    )
                    connection.request(
                        "HEAD",
                        path,
                        headers={"User-Agent": "Experience-Callback-Check/1.0"},
                    )
                    status = connection.getresponse().status
                finally:
                    connection.close()
    except (OSError, ValueError, http.client.HTTPException, ValidationError) as exc:
        error = (
            " ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else "Endpoint unreachable or TLS validation failed."
        )
    with transaction.atomic():
        current = ProductCredential.objects.select_for_update().get(pk=credential.pk)
        if current.callback_url != url:
            return current
        current.last_checked_at = timezone.now()
        current.last_status = status
        current.last_latency_ms = round((time.monotonic() - started) * 1000)
        current.last_error = error
        current.consecutive_failures = (
            0
            if status and HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES
            else current.consecutive_failures + 1
        )
        current.save()
        if current.consecutive_failures == CALLBACK_FAILURE_ALERT_THRESHOLD:
            notify_integrators(
                current.product.organisation,
                f"{current.product.workspace.definition.short_name}: "
                "callback check failed three times",
                f"The callback for {current.product.name} is not responding. "
                "Check the integration URLs in the portal.",
            )
        audit(
            actor=actor,
            action="Callback checked",
            product=current.product,
            detail={
                "status": status,
                "error": error,
                "latency_ms": current.last_latency_ms,
            },
        )
        return current
