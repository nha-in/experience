"""The provisioning chain — Keycloak, then WSO2, then HIE-CM.

Order is forced by the data: WSO2 maps the Keycloak client's credentials as its
consumer key, and the bridge is named after that same client. Each step is
ledger-guarded, so a chain that dies half-way and is re-run finishes the missing
systems instead of creating a second set.

The correlation id travels as a message header, bound before every task body by
`config.celery_app`. A `ContextVar` does not survive `on_commit` -> broker ->
worker, so it has to cross as data — but as a header rather than an argument,
which is what stops a task from being written that quietly forgets to carry it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from celery import chain
from celery import shared_task
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ohc_experience.experiences.models import Product
from ohc_experience.experiences.workflows import audit
from ohc_experience.experiences.workflows import notify_integrators
from ohc_experience.integrations.correlation import get_correlation_id
from ohc_experience.integrations.credentials import publish_credential
from ohc_experience.integrations.keycloak.roles import role_names_for
from ohc_experience.integrations.models import TEARDOWN_PENDING_STATES
from ohc_experience.integrations.models import ProvisionedResource
from ohc_experience.integrations.models import ProvisionedResourceState
from ohc_experience.integrations.models import ProvisionedSystem
from ohc_experience.integrations.models import ProvisioningRun
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import BridgeSpec
from ohc_experience.integrations.ports import ClientSpec
from ohc_experience.integrations.ports import GatewayAppSpec
from ohc_experience.integrations.registry import get_api_gateway
from ohc_experience.integrations.registry import get_bridge_registry
from ohc_experience.integrations.registry import get_idp_admin
from ohc_experience.integrations.secret_ref import has_secret
from ohc_experience.integrations.secret_ref import store_secret
from ohc_experience.integrations.wso2.apis import api_ids_for

if TYPE_CHECKING:
    from collections.abc import Callable

    from celery import Task
    from django.contrib.auth.models import AbstractBaseUser

logger = logging.getLogger(__name__)

CONFIG_ERROR = "CONFIG_ERROR"


@dataclass(frozen=True, slots=True)
class _Failure:
    code: str
    detail: str
    retryable: bool


def _backoff(retries: int) -> float:
    delay = settings.PROVISIONING_RETRY_BACKOFF_SECONDS * (2**retries)
    return min(delay, settings.PROVISIONING_RETRY_BACKOFF_MAX_SECONDS)


def _ledger_row(
    product: Product,
    system: ProvisionedSystem,
) -> ProvisionedResource | None:
    return ProvisionedResource.objects.filter(product=product, system=system).first()


def _record(
    product: Product,
    system: ProvisionedSystem,
    *,
    external_ref: str,
    public_ref: str = "",
    secret_ref: str = "",
) -> ProvisionedResource:
    """Write the ledger the instant the external system says yes.

    The gap between the remote create and this write only orphans a Keycloak
    client: WSO2 create-or-looks-up and HIE-CM upserts, but a random client id
    dies with the task that made it.
    """
    row, _created = ProvisionedResource.objects.update_or_create(
        product=product,
        system=system,
        defaults={
            "external_ref": external_ref,
            "public_ref": public_ref,
            "secret_ref": secret_ref,
            "state": ProvisionedResourceState.ACTIVE,
        },
    )
    return row


def _open_run(product: Product) -> ProvisioningRun | None:
    return (
        ProvisioningRun.objects.filter(
            product=product,
            status=ProvisioningRun.Status.RUNNING,
        )
        .order_by("-started_at")
        .first()
    )


def _close_run(product: Product, status: str, error: str = "") -> None:
    run = _open_run(product)
    if run is None:
        return
    run.status = status
    run.error = error
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at", "updated_at"])


def _fail(
    task: Task,
    product: Product,
    system: ProvisionedSystem,
    failure: _Failure,
) -> None:
    """Retry while it can plausibly help, then close the attempt.

    Closed here, not by `complete_provisioning`, which only sees *that* the
    ledger is short — not why.
    """
    attempts = task.request.retries + 1
    if failure.retryable and attempts < settings.PROVISIONING_MAX_ATTEMPTS:
        raise task.retry(countdown=_backoff(task.request.retries))

    # No row is written for a system that produced nothing: absence already
    # means "not provisioned", and a phantom row with an empty ref would have to
    # be reasoned about by every reader of the ledger.
    row = _ledger_row(product, system)
    if row is not None:
        row.state = ProvisionedResourceState.FAILED
        row.save(update_fields=["state", "updated_at"])

    detail = f"{system}/{failure.code}: {failure.detail}"
    logger.error("provisioning failed for %s: %s", _reference(product), detail)
    _close_run(product, ProvisioningRun.Status.FAILED, detail)
    audit(
        actor=None,
        action="Provisioning failed",
        product=product,
        detail={
            "system": str(system),
            "code": failure.code,
            "attempts": attempts,
            "detail": detail[: settings.PROVISIONING_DETAIL_MAX_CHARS],
        },
    )


def _step(
    task: Task,
    product_id: int,
    system: ProvisionedSystem,
    call: Callable[[Product], None],
) -> int:
    """Shared shape of every step: skip what is settled, call, or fail.

    `ImproperlyConfigured` is caught alongside the adapter errors on purpose — a
    missing API-name list is not a transient fault, and retrying it five times
    over half an hour only delays the operator finding out.
    """
    product = Product.objects.get(pk=product_id)

    # An earlier link closed this attempt; later links must not carry on, or a
    # WSO2 failure still builds a bridge. No attempt record at all means a task
    # invoked directly, which has nothing to abandon.
    if product.provisioning_runs.exists() and _open_run(product) is None:
        return product_id

    # This system is already done — the whole of what makes a retry safe.
    row = _ledger_row(product, system)
    if row is not None and row.state == ProvisionedResourceState.ACTIVE:
        return product_id

    try:
        call(product)
    except AdapterError as error:
        _fail(
            task,
            product,
            system,
            _Failure(error.code, error.message, retryable=error.retryable),
        )
    except ImproperlyConfigured as error:
        _fail(
            task,
            product,
            system,
            _Failure(CONFIG_ERROR, str(error), retryable=False),
        )

    return product_id


@shared_task(bind=True, max_retries=None)
def provision_keycloak(task: Task, product_id: int) -> int:
    def run(product: Product) -> None:
        created = get_idp_admin().create_client(
            ClientSpec(
                reference=_reference(product),
                display_name=_external_name(product),
                role_names=role_names_for(_program(product)),
            ),
        )
        _record(
            product,
            ProvisionedSystem.KEYCLOAK,
            external_ref=created.external_id,
            public_ref=created.client_id,
            # Parked, never persisted: it is read once and the TTL clears it.
            secret_ref=store_secret(created.initial_secret),
        )

    return _step(task, product_id, ProvisionedSystem.KEYCLOAK, run)


def _live_secret_ref(client: ProvisionedResource) -> str:
    """The parked secret, re-minted if it aged out while WSO2 was unreachable.

    `SECRET_REF_TTL_SECONDS` is deliberately short, and shorter than this chain's
    own retry budget: a WSO2 outage lasting past the TTL would otherwise reach a
    step that can never succeed, since the Keycloak row is already ACTIVE and
    nothing would mint a replacement.
    """
    if has_secret(client.secret_ref):
        return client.secret_ref

    rotated = get_idp_admin().rotate_client_secret(client.external_ref)
    client.secret_ref = store_secret(rotated.secret)
    client.save(update_fields=["secret_ref", "updated_at"])
    return client.secret_ref


@shared_task(bind=True, max_retries=None)
def provision_wso2(task: Task, product_id: int) -> int:
    def run(product: Product) -> None:
        client = _ledger_row(product, ProvisionedSystem.KEYCLOAK)
        if client is None:
            message = "WSO2 needs the Keycloak client that should already exist"
            raise ImproperlyConfigured(message)

        api_ids = api_ids_for(_program(product))
        gateway = get_api_gateway()
        created = gateway.create_application(
            GatewayAppSpec(
                reference=_reference(product),
                name=_external_name(product),
                api_ids=api_ids,
            ),
        )
        gateway.subscribe(created.external_id, api_ids)
        gateway.map_keys(
            created.external_id,
            consumer_key=client.public_ref,
            secret_ref=_live_secret_ref(client),
        )
        _record(
            product,
            ProvisionedSystem.WSO2,
            external_ref=created.external_id,
            public_ref=created.name,
        )

    return _step(task, product_id, ProvisionedSystem.WSO2, run)


@shared_task(bind=True, max_retries=None)
def provision_hiecm(task: Task, product_id: int) -> int:
    def run(product: Product) -> None:
        client = _ledger_row(product, ProvisionedSystem.KEYCLOAK)
        if client is None:
            message = "the bridge is named after a Keycloak client that is missing"
            raise ImproperlyConfigured(message)

        bridge_id = client.public_ref
        get_bridge_registry().create_bridge(
            BridgeSpec(
                bridge_id=bridge_id,
                name=_external_name(product),
                url=_callback_url(product),
                entity=_bridge_entity(product),
            ),
        )
        _record(
            product,
            ProvisionedSystem.HIECM,
            external_ref=bridge_id,
            public_ref=bridge_id,
        )

    return _step(task, product_id, ProvisionedSystem.HIECM, run)


def _abandon(task: Task, product: Product, detail: str) -> None:
    """Close the attempt when every system is up but the credential never landed.

    Without this the run stays RUNNING, which no retry treats as recoverable.
    """
    logger.error(
        "publishing credentials for %s failed: %s",
        _reference(product),
        detail,
    )
    _close_run(product, ProvisioningRun.Status.FAILED, detail)
    audit(
        actor=None,
        action="Provisioning failed",
        product=product,
        detail={
            "system": "CREDENTIAL",
            "attempts": task.request.retries + 1,
            "detail": detail[: settings.PROVISIONING_DETAIL_MAX_CHARS],
        },
    )


@shared_task(bind=True, max_retries=None)
def complete_provisioning(task: Task, product_id: int) -> int:
    """Only the ledger decides this — never "the chain got this far"."""
    product = Product.objects.get(pk=product_id)

    done = set(
        ProvisionedResource.objects.filter(
            product=product,
            state=ProvisionedResourceState.ACTIVE,
        ).values_list("system", flat=True),
    )
    missing = set(ProvisionedSystem.values) - done
    if missing:
        # A step that already failed recorded a better reason than anything
        # derivable here. An open attempt means the ledger is short with
        # nothing to blame, which is the case worth reporting.
        if _open_run(product) is not None:
            logger.error(
                "provisioning for %s reached completion missing %s",
                _reference(product),
                sorted(missing),
            )
            _close_run(
                product,
                ProvisioningRun.Status.FAILED,
                f"incomplete ledger: missing {', '.join(sorted(missing))}",
            )
            audit(
                actor=None,
                action="Provisioning incomplete",
                product=product,
                detail={"missing": sorted(missing)},
            )
        return product_id

    try:
        publish_credential(product)
    except AdapterError as error:
        if (
            error.retryable
            and task.request.retries + 1 < settings.PROVISIONING_MAX_ATTEMPTS
        ):
            raise task.retry(countdown=_backoff(task.request.retries)) from error
        _abandon(task, product, f"{error.code}: {error.message}")
        return product_id
    except ValidationError as error:
        _abandon(task, product, "; ".join(error.messages))
        return product_id

    _close_run(product, ProvisioningRun.Status.READY)
    audit(
        actor=None,
        action="Provisioning completed",
        product=product,
        detail={"systems": sorted(done)},
    )
    notify_integrators(
        product.organisation,
        f"{product.workspace.definition.short_name}: credentials available",
        f"Credentials for {product.name} are available in the portal.",
    )
    return product_id


_NON_ALPHANUMERIC = re.compile(r"[^a-zA-Z0-9]+")


def _reference(product: Product) -> str:
    return product.workspace.reference


def _program(product: Product) -> str:
    return product.workspace.experience_type


def _external_name(product: Product) -> str:
    """The integrator's name as the three systems will accept it."""
    name = _NON_ALPHANUMERIC.sub(" ", product.organisation.display_name).strip()
    return name or _NON_ALPHANUMERIC.sub(" ", _reference(product)).strip()


def _callback_url(product: Product) -> str:
    """Where HIE-CM delivers this integrator's gateway callbacks.

    Ours rather than the integrator's own endpoint, which is not known until
    they save it — every bridge pointing at one shared bin is what this avoids.
    """
    base = settings.HIECM_BRIDGE_CALLBACK_BASE_URL.rstrip("/")
    return f"{base}/{_reference(product)}"


def _bridge_entity(product: Product) -> str:
    """The bridge's `entity`, valued as legacy sent it."""
    entity_type = product.organisation.entity_type
    if entity_type == "sole_proprietor":
        return "NA"
    if entity_type == "government":
        return "Government"
    return "Private"


#: The chain, in the order the data forces.
CHAIN = (provision_keycloak, provision_wso2, provision_hiecm, complete_provisioning)


def enqueue_chain(
    product: Product,
    started_by: AbstractBaseUser | None = None,
) -> None:
    """Schedule the whole chain for after the caller's transaction commits.

    Opens the attempt record first, so a run that dies before any adapter
    returns still leaves a row saying it was tried.
    """
    product_id = product.pk
    ProvisioningRun.objects.create(
        product=product,
        correlation_id=get_correlation_id() or "",
        started_by=started_by,
    )

    def _send() -> None:
        # `.si`, not `.s`: an immutable signature ignores the previous task's
        # return value, so each link states the product it is for instead of
        # inheriting it.
        chain(*(task.si(product_id) for task in CHAIN)).delay()

    transaction.on_commit(_send)


def _teardown_failed(
    task: Task,
    row: ProvisionedResource,
    failure: _Failure,
) -> None:
    """Mark this one resource failed. Deliberately does not raise.

    Provisioning stops at the first failure because there is no point building a
    bridge for a client that does not exist. Teardown is the opposite: every
    resource left switched on is a live credential for a revoked integrator, so
    a step that gives up must still let the next one run.
    """
    attempts = task.request.retries + 1
    if failure.retryable and attempts < settings.PROVISIONING_MAX_ATTEMPTS:
        raise task.retry(countdown=_backoff(task.request.retries))

    row.state = ProvisionedResourceState.FAILED
    row.save(update_fields=["state", "updated_at"])
    logger.error(
        "deprovisioning %s for product %s failed after %s attempts: %s: %s",
        row.system,
        row.product_id,
        attempts,
        failure.code,
        failure.detail,
    )


def _teardown_step(
    task: Task,
    product_id: int,
    system: ProvisionedSystem,
    call: Callable[[Product, ProvisionedResource], None],
) -> int:
    product = Product.objects.get(pk=product_id)

    # Missing means nothing was ever created and DISABLED means a previous run
    # finished the job; both are success. FAILED is not — that is a resource we
    # tried and failed to switch off, and it is precisely what a retry is for.
    row = _ledger_row(product, system)
    if row is None or row.state not in TEARDOWN_PENDING_STATES:
        return product_id

    try:
        call(product, row)
    except AdapterError as error:
        _teardown_failed(
            task,
            row,
            _Failure(error.code, error.message, retryable=error.retryable),
        )
        return product_id
    except ImproperlyConfigured as error:
        _teardown_failed(task, row, _Failure(CONFIG_ERROR, str(error), retryable=False))
        return product_id

    row.state = ProvisionedResourceState.DISABLED
    row.save(update_fields=["state", "updated_at"])
    return product_id


@shared_task(bind=True, max_retries=None)
def deprovision_keycloak(task: Task, product_id: int) -> int:
    def run(_product: Product, row: ProvisionedResource) -> None:
        get_idp_admin().disable_client(row.external_ref)

    return _teardown_step(task, product_id, ProvisionedSystem.KEYCLOAK, run)


@shared_task(bind=True, max_retries=None)
def deprovision_wso2(task: Task, product_id: int) -> int:
    def run(product: Product, row: ProvisionedResource) -> None:
        # The same source provisioning subscribed from. If the configured set
        # has changed since, the difference is left behind rather than guessed.
        get_api_gateway().unsubscribe(
            row.external_ref,
            api_ids_for(_program(product)),
        )

    return _teardown_step(task, product_id, ProvisionedSystem.WSO2, run)


@shared_task(bind=True, max_retries=None)
def deprovision_hiecm(task: Task, product_id: int) -> int:
    def run(_product: Product, row: ProvisionedResource) -> None:
        get_bridge_registry().deactivate_bridge(row.external_ref)

    return _teardown_step(task, product_id, ProvisionedSystem.HIECM, run)


#: The reverse chain. Keycloak first: it is the only step that stops tokens.
TEARDOWN = (deprovision_keycloak, deprovision_wso2, deprovision_hiecm)


def enqueue_teardown(product: Product) -> None:
    """Schedule the reverse chain for after the caller's transaction commits."""
    product_id = product.pk

    def _send() -> None:
        chain(*(task.si(product_id) for task in TEARDOWN)).delay()

    transaction.on_commit(_send)
