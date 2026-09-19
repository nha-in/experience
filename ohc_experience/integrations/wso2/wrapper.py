"""WSO2 through NHA's wrapper: one call in place of the DevPortal sequence.

Legacy provisions this way. `WorkflowServiceImpl` posts an application name and
the Keycloak credentials to `/add-subscriptions` and lets the wrapper create the
application, map the keys and subscribe; its own DevPortal sequence — the one
`Wso2ApiGateway` reimplements — sits commented out directly above that call.

Chosen when the DevPortal is unreachable but the wrapper is. It cannot
deprovision: legacy has no endpoint for that, so `unsubscribe` fails loudly
rather than reporting a revoked integrator as torn down.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings

from ohc_experience.integrations.http import HttpPolicy
from ohc_experience.integrations.http import IntegrationClient
from ohc_experience.integrations.naming import APP_NAME_TEMPLATE
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.ports import GatewayAppCreated
from ohc_experience.integrations.secret_ref import resolve_secret

if TYPE_CHECKING:
    import httpx

    from ohc_experience.integrations.ports import GatewayAppSpec


class Wso2WrapperApiGateway:
    """Implements `ApiGateway` over NHA's wrapper service."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._path = settings.WSO2_WRAPPER_PATH
        policy = HttpPolicy(
            system=ExternalSystem.WSO2,
            base_url=settings.WSO2_WRAPPER_BASE_URL.rstrip("/"),
            read_timeout=settings.WSO2_READ_TIMEOUT_SECONDS,
        )
        self._client = IntegrationClient(policy, transport=transport)

    def create_application(self, spec: GatewayAppSpec) -> GatewayAppCreated:
        """Names what `map_keys` will create. The wrapper has nothing to call yet.

        The name matches `Wso2ApiGateway`'s, so switching back to the DevPortal
        adapter finds the application this one asked the wrapper to make.
        """
        name = APP_NAME_TEMPLATE.format(reference=spec.reference)
        return GatewayAppCreated(external_id=name, name=name)

    def subscribe(self, external_id: str, api_ids: tuple[str, ...]) -> None:
        """The wrapper subscribes from its own configured list, inside `map_keys`."""

    def map_keys(self, external_id: str, consumer_key: str, secret_ref: str) -> None:
        """All of provisioning: the wrapper creates, maps and subscribes on this POST.

        Not retried, and not idempotent as far as we know — a second POST for the
        same application is the wrapper's to reject. The ledger keeps that to the
        narrow window where this call succeeded but the step failed afterwards.
        """
        self._client.request(
            "POST",
            self._path,
            op="add_subscriptions",
            json={
                "applicationName": external_id,
                "consumerKey": consumer_key,
                "consumerSecret": resolve_secret(secret_ref, ExternalSystem.WSO2),
            },
        )

    def unsubscribe(self, external_id: str, api_ids: tuple[str, ...]) -> None:
        raise AdapterError(
            ExternalSystem.WSO2,
            "UNSUPPORTED",
            retryable=False,
            message=(
                f"the wrapper has no endpoint to unsubscribe {external_id}; "
                "remove the WSO2 application by hand"
            ),
        )

    def close(self) -> None:
        self._client.close()
