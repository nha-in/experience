import base64
import hashlib
import http.client
import ipaddress
import secrets
import socket
import ssl
import time
from datetime import timedelta
from http import HTTPStatus
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductOutcome
from ohc_experience.organisations.models import Organisation

from .models import SandboxCredential
from .services import audit
from .services import notify_integrators
from .services import require_integrator

CALLBACK_FAILURE_ALERT_THRESHOLD = 3


def cipher():
    key = settings.SANDBOX_CREDENTIAL_KEY
    if not key:
        if not settings.SANDBOX_ALLOW_DEMO_CREDENTIALS:
            msg = "Set SANDBOX_CREDENTIAL_KEY to a dedicated Fernet key."
            raise ImproperlyConfigured(
                msg,
            )
        key = base64.urlsafe_b64encode(
            hashlib.sha256(settings.SECRET_KEY.encode()).digest(),
        )
    return Fernet(key)


def _credentials(product, operation):
    if settings.SANDBOX_CREDENTIAL_PROVIDER:
        return import_string(settings.SANDBOX_CREDENTIAL_PROVIDER)(
            product=product,
            operation=operation,
        )
    if not settings.SANDBOX_ALLOW_DEMO_CREDENTIALS:
        msg = "Configure a sandbox gateway credential provider before issuance."
        raise ImproperlyConfigured(
            msg,
        )
    return {
        "client_id": f"demo_{product.workspace.sandbox_id.lower()}",
        "client_secret": secrets.token_urlsafe(32),
        "gateway_url": settings.SANDBOX_GATEWAY_URL,
    }


def _outcome(credential, actor):
    application = credential.product.sandbox_reviews.get(
        kind="product_registration",
    ).application
    ProductOutcome.objects.update_or_create(
        product=credential.product,
        source_application=application,
        outcome_type="sandbox_credentials",
        defaults={
            "name": "Sandbox credentials",
            "status": credential.status,
            "issued_by": actor,
            "data": {
                "client_id": credential.client_id,
                "gateway_url": credential.gateway_url,
                "credential_id": credential.pk,
                "demo": settings.SANDBOX_ALLOW_DEMO_CREDENTIALS
                and not settings.SANDBOX_CREDENTIAL_PROVIDER,
            },
            "metadata": {"secret_access": "audited_reveal_endpoint"},
        },
    )


@transaction.atomic
def issue_credentials(product, actor):
    organisation = Organisation.objects.select_for_update().get(
        pk=product.organisation_id,
    )
    product = Product.objects.select_for_update().get(pk=product.pk)
    if not organisation.is_verified:
        msg = "Credentials require a verified organisation."
        raise ValidationError(msg)
    existing = SandboxCredential.objects.filter(product=product).first()
    if existing:
        return existing
    values = _credentials(product, "issue")
    credential = SandboxCredential.objects.create(
        product=product,
        client_id=values["client_id"],
        encrypted_secret=cipher().encrypt(values["client_secret"].encode()).decode(),
        gateway_url=values["gateway_url"],
        rotation_due=timezone.now() + timedelta(days=90),
    )
    _outcome(credential, actor)
    audit(
        actor=actor,
        action="Sandbox credentials issued",
        product=product,
        detail={"client_id": credential.client_id},
    )
    notify_integrators(
        product.organisation,
        "ABDM: sandbox credentials available",
        f"Credentials for {product.name} are available in the portal. "
        "Use synthetic data only.",
    )
    return credential


def rate_limit(actor, operation, limit=5):
    key = f"sandbox:{operation}:{actor.pk}:{int(time.time()) // 60}"
    if cache.add(key, 1, timeout=65):
        return
    if cache.incr(key) > limit:
        msg = "Too many requests. Wait a minute and try again."
        raise ValidationError(msg)


def _locked_credential(credential):
    organisation_id = SandboxCredential.objects.values_list(
        "product__organisation_id",
        flat=True,
    ).get(pk=credential.pk)
    Organisation.objects.select_for_update().get(pk=organisation_id)
    return SandboxCredential.objects.select_for_update().get(pk=credential.pk)


@transaction.atomic
def reveal(credential, actor):
    credential = _locked_credential(credential)
    require_integrator(actor, credential.product.organisation)
    rate_limit(actor, "reveal")
    if credential.status != "active" or not credential.product.organisation.is_verified:
        msg = "These credentials are no longer active."
        raise ValidationError(msg)
    audit(actor=actor, action="Client secret revealed", product=credential.product)
    return cipher().decrypt(credential.encrypted_secret.encode()).decode()


@transaction.atomic
def rotate(credential, actor):
    credential = _locked_credential(credential)
    require_integrator(actor, credential.product.organisation)
    if not credential.product.organisation.is_verified:
        msg = "Credentials require a verified organisation."
        raise ValidationError(msg)
    rate_limit(actor, "rotate", limit=2)
    values = _credentials(credential.product, "rotate")
    credential.client_id = values["client_id"]
    credential.encrypted_secret = (
        cipher().encrypt(values["client_secret"].encode()).decode()
    )
    credential.status = "active"
    credential.issued_at = timezone.now()
    credential.rotation_due = timezone.now() + timedelta(days=90)
    credential.save()
    _outcome(credential, actor)
    audit(actor=actor, action="Sandbox credentials rotated", product=credential.product)


def _revoke(credential, actor):
    if settings.SANDBOX_CREDENTIAL_PROVIDER:
        _credentials(credential.product, "revoke")
    credential.status = "revoked"
    credential.encrypted_secret = ""
    credential.save(update_fields=["status", "encrypted_secret"])
    _outcome(credential, actor)
    audit(actor=actor, action="Sandbox credentials revoked", product=credential.product)


@transaction.atomic
def revoke(credential, actor):
    credential = _locked_credential(credential)
    require_integrator(actor, credential.product.organisation)
    if credential.status == "active":
        _revoke(credential, actor)


def suspend_organisation_credentials(organisation, actor):
    for credential in SandboxCredential.objects.select_for_update().filter(
        product__organisation=organisation,
        status="active",
    ):
        _revoke(credential, actor)


@transaction.atomic
def save_urls(credential, actor, cleaned_data):
    require_integrator(actor, credential.product.organisation)
    credential = SandboxCredential.objects.select_for_update().get(pk=credential.pk)
    old = {key: getattr(credential, key) for key in cleaned_data}
    for key in ("callback_url", "bridge_url"):
        setattr(credential, key, cleaned_data[key])
    if old["callback_url"] != credential.callback_url:
        credential.last_checked_at = None
        credential.last_status = None
        credential.last_latency_ms = None
        credential.last_error = ""
        credential.consecutive_failures = 0
    credential.save()
    audit(
        actor=actor,
        action="Integration URLs updated",
        product=credential.product,
        detail={"before": old, "after": cleaned_data},
    )


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
                        headers={"User-Agent": "ABDM-Sandbox-Callback-Check/1.0"},
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
        current = SandboxCredential.objects.select_for_update().get(pk=credential.pk)
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
                "ABDM: callback check failed three times",
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
