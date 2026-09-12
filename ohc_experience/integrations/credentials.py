"""What the chain leaves behind, and the one operation a panel performs on it.

Separate from `services.py`: that module imports `tasks`, which imports the
adapter packages by name, and a view may not reach those even transitively. A
view needs these functions and nothing else in the chain.

Permission checks belong to the caller. Nothing here asks who is asking.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.utils import timezone

from ohc_experience.experiences.definitions import OutcomeDefinition
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.experiences.secrets import cipher
from ohc_experience.experiences.services import issue_outcome
from ohc_experience.integrations.models import ProvisionedResource
from ohc_experience.integrations.models import ProvisionedResourceState
from ohc_experience.integrations.models import ProvisionedSystem
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.registry import get_idp_admin
from ohc_experience.integrations.secret_ref import discard_secret
from ohc_experience.integrations.secret_ref import has_secret
from ohc_experience.integrations.secret_ref import resolve_secret

if TYPE_CHECKING:
    from ohc_experience.experiences.models import Product


def _definition(product: Product):
    return product.workspace.definition.credentials


def _keycloak_client(product: Product) -> ProvisionedResource | None:
    return ProvisionedResource.objects.filter(
        product=product,
        system=ProvisionedSystem.KEYCLOAK,
        state=ProvisionedResourceState.ACTIVE,
    ).first()


def _forget_secret_ref(row: ProvisionedResource) -> None:
    discard_secret(row.secret_ref)
    row.secret_ref = ""
    row.save(update_fields=["secret_ref", "updated_at"])


def _issued_secret(client: ProvisionedResource) -> str:
    """The secret the chain parked, or a fresh one if the short TTL passed."""
    if has_secret(client.secret_ref):
        return resolve_secret(client.secret_ref, ExternalSystem.KEYCLOAK)
    return get_idp_admin().rotate_client_secret(client.external_ref).secret


def publish_credential(product: Product) -> ProductCredential:
    """Surface the finished chain as the row the integrator's panel reads."""
    definition = _definition(product)
    client = _keycloak_client(product)
    if client is None:
        message = "provisioning completed without a Keycloak client"
        raise ValidationError(message)

    secret = _issued_secret(client)
    credential, _created = ProductCredential.objects.update_or_create(
        product=product,
        environment=ProductCredential.Environment.SANDBOX,
        defaults={
            "client_id": client.public_ref,
            "encrypted_secret": cipher().encrypt(secret.encode()).decode(),
            "gateway_url": definition.gateway_url(),
            "status": "active",
            "issued_at": timezone.now(),
            "rotation_due": timezone.now() + timedelta(days=definition.rotation_days),
        },
    )
    _forget_secret_ref(client)

    application = product.review_items.get(kind=ReviewItem.Kind.PRODUCT).application
    issue_outcome(
        application=application,
        actor=None,
        outcome=OutcomeDefinition(
            key=definition.outcome_type,
            name=definition.name,
            status=credential.status,
            data={
                "client_id": credential.client_id,
                "gateway_url": credential.gateway_url,
                "credential_id": credential.pk,
                "demo": definition.is_demo(),
            },
            metadata={"secret_access": "audited_reveal_endpoint"},
        ),
    )
    return credential


def rotate_client(product: Product) -> str:
    """Mint a new Keycloak secret and hand it back for the caller to store.

    Keycloak is the only system touched. WSO2 keeps the key mapping made at
    provisioning, which now holds the previous secret; the gateway validates the
    JWT rather than the secret, so this is expected to be harmless — but it is
    unverified against a real gateway.
    """
    row = _keycloak_client(product)
    if row is None:
        message = "There are no credentials to rotate yet."
        raise ValidationError(message)

    return get_idp_admin().rotate_client_secret(row.external_ref).secret
