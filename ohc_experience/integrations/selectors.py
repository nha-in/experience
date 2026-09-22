"""Reads over the provisioning ledger, for the credentials panel.

Nothing here returns a secret. The one value that is a secret lives in the
short-lived hand-off and is read exactly once, by `credentials.take_initial_secret`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.utils.translation import gettext_lazy as _

from ohc_experience.integrations.models import TEARDOWN_PENDING_STATES
from ohc_experience.integrations.models import ProvisionedResource
from ohc_experience.integrations.models import ProvisionedResourceState
from ohc_experience.integrations.models import ProvisionedSystem
from ohc_experience.integrations.models import ProvisioningRun

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise

    from ohc_experience.experiences.models import Product

#: What the integrator is told each system is for. The ledger's own names are
#: vendor names; these are the ones on screen.
SYSTEM_LABELS: dict[str, StrOrPromise] = {
    ProvisionedSystem.KEYCLOAK: _("Identity"),
    ProvisionedSystem.WSO2: _("Gateway"),
    ProvisionedSystem.HIECM: _("Bridge"),
}

#: The bridge is left out: it would read "Not set up" forever for the
#: integrators who never need one.
CHAIN_SYSTEMS = (ProvisionedSystem.KEYCLOAK, ProvisionedSystem.WSO2)

_VARIANTS = {
    ProvisionedResourceState.ACTIVE: "success",
    ProvisionedResourceState.FAILED: "destructive",
    ProvisionedResourceState.DISABLED: "neutral",
    ProvisionedResourceState.ORPHANED: "warning",
    ProvisionedResourceState.LEFT_SUBSCRIBED: "neutral",
}


def _display(row: ProvisionedResource) -> StrOrPromise:
    """A gateway left subscribed reads as disabled: with the Keycloak client off
    nothing can use it, and there is nothing an admin can do about it."""
    if row.state == ProvisionedResourceState.LEFT_SUBSCRIBED:
        return ProvisionedResourceState.DISABLED.label
    return row.get_state_display()


@dataclass(frozen=True, slots=True)
class SystemProgress:
    system: str
    label: StrOrPromise
    state: str
    display: StrOrPromise
    variant: str


def provisioning_progress(product: Product) -> list[SystemProgress]:
    """One row per system, in chain order, including the ones not reached yet.

    A system with no ledger row is shown rather than omitted: "we have not
    started the bridge" and "there is no bridge" look identical if you only
    render what exists, and the first is the common case mid-chain.

    Empty when the chain has not run at all, so a caller can use it to decide
    whether there is anything to show.
    """
    rows = {
        row.system: row
        for row in ProvisionedResource.objects.filter(
            product=product,
            system__in=CHAIN_SYSTEMS,
        )
    }
    if not rows:
        return []

    run = latest_run(product)
    pending = _("Waiting") if run is not None and run.is_running else _("Not set up")
    return [
        SystemProgress(
            system=system,
            label=SYSTEM_LABELS[system],
            state=rows[system].state if system in rows else "",
            display=_display(rows[system]) if system in rows else pending,
            variant=_VARIANTS.get(rows[system].state, "neutral")
            if system in rows
            else "neutral",
        )
        for system in CHAIN_SYSTEMS
    ]


def awaiting_provisioning(product: Product) -> bool:
    """No chain has ever been started for this product."""
    return not product.provisioning_runs.exists()


def latest_run(product: Product) -> ProvisioningRun | None:
    return product.provisioning_runs.order_by("-started_at").first()


def provisioning_can_be_retried(product: Product) -> bool:
    """Only a failed attempt is worth re-running.

    A RUNNING one would race the chain already in flight, and a READY one has
    nothing left to ask any system for.
    """
    run = latest_run(product)
    return run is not None and run.status == ProvisioningRun.Status.FAILED


def teardown_is_incomplete(product: Product) -> bool:
    """Whether anything is still switched on. Every row left is a live credential."""
    return ProvisionedResource.objects.filter(
        product=product,
        state__in=TEARDOWN_PENDING_STATES,
    ).exists()


def bridge_state(product: Product) -> str:
    """What to tell the integrator about their callback's registration.

    "none" covers both "no URL saved" and "saved, worker not there yet"; the
    page tells those apart, because it knows whether a URL is saved.
    """
    row = ProvisionedResource.objects.filter(
        product=product,
        system=ProvisionedSystem.HIECM,
    ).first()
    if row is None:
        return "none"
    if row.state == ProvisionedResourceState.ACTIVE:
        return "registered"
    if row.state == ProvisionedResourceState.FAILED:
        return "failed"
    return "none"
