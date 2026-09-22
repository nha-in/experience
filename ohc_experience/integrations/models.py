"""What we created in each external system, so a partial run can be reconciled.

`secret_ref` is a reference into the short-lived hand-off, never a secret value.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ProvisionedSystem(models.TextChoices):
    KEYCLOAK = "KEYCLOAK", _("Keycloak")
    WSO2 = "WSO2", _("WSO2")
    HIECM = "HIECM", _("HIE-CM")


class ProvisionedResourceState(models.TextChoices):
    ACTIVE = "ACTIVE", _("Active")
    DISABLED = "DISABLED", _("Disabled")
    FAILED = "FAILED", _("Failed")
    #: Exists in the external system with no live owner here.
    ORPHANED = "ORPHANED", _("Orphaned")
    #: Revoked, but the system offers no call to remove it; see the WSO2 wrapper.
    #: Shown as disabled, since nothing can use it with the Keycloak client off.
    LEFT_SUBSCRIBED = "LEFT_SUBSCRIBED", _("Left subscribed")


class ProvisionedResource(models.Model):
    product = models.ForeignKey(
        "experiences.Product",
        on_delete=models.PROTECT,
        related_name="provisioned_resources",
    )
    system = models.CharField(max_length=20, choices=ProvisionedSystem)
    external_ref = models.CharField(max_length=255)
    #: The handle a person sees, where it differs from the one we call the API
    #: with. Only Keycloak has both: `external_ref` is the internal UUID that
    #: `disable_client` and `rotate_client_secret` take, while the OAuth
    #: `clientId` is what the integrator is shown, what WSO2 maps as its
    #: consumer key, and what the HIE-CM bridge is named after.
    public_ref = models.CharField(max_length=255, blank=True)
    secret_ref = models.CharField(max_length=255, blank=True)
    state = models.CharField(
        max_length=20,
        choices=ProvisionedResourceState,
        default=ProvisionedResourceState.ACTIVE,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # The idempotency backstop: a retried chain must not create a second
            # client for the same product in the same system.
            models.UniqueConstraint(
                fields=["product", "system"],
                name="integrations_provisioned_resource_unique_product_system",
            ),
            models.CheckConstraint(
                condition=models.Q(system__in=ProvisionedSystem.values),
                name="integrations_provisioned_resource_system_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=ProvisionedResourceState.values),
                name="integrations_provisioned_resource_state_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["system", "state"],
                name="integrations_system_state_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.system}:{self.external_ref} ({self.state})"


#: States a teardown step still has work to do in. ORPHANED is excluded: it means
#: the resource has no live owner here, so it is a reconciliation sweep's to clean.
TEARDOWN_PENDING_STATES = frozenset(
    {ProvisionedResourceState.ACTIVE, ProvisionedResourceState.FAILED},
)


class ProvisioningRun(models.Model):
    """One attempt, and how it went.

    The chain writes a `ProvisionedResource` only for a system that produced
    something — absence is how "not provisioned" is expressed — so a run that
    failed before creating anything has nowhere else to be recorded.
    """

    class Status(models.TextChoices):
        RUNNING = "running", _("Running")
        READY = "ready", _("Ready")
        FAILED = "failed", _("Failed")

    product = models.ForeignKey(
        "experiences.Product",
        on_delete=models.CASCADE,
        related_name="provisioning_runs",
        verbose_name=_("Product"),
    )
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
    )
    correlation_id = models.CharField(_("Correlation id"), max_length=64, blank=True)
    error = models.TextField(_("Error"), blank=True)

    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="provisioning_runs_started",
        help_text=_("Null when registration started it rather than a person."),
    )

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Provisioning run")
        verbose_name_plural = _("Provisioning runs")
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.product} ({self.status})"

    @property
    def is_running(self) -> bool:
        return self.status == self.Status.RUNNING
