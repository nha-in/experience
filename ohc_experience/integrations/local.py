"""In-process stand-ins for every port, so the portal runs with no VPN.

State lives in Django's cache rather than module globals because the web
container and the Celery worker are separate processes: a client created by a
provisioning task has to be visible to the request that renders the credentials
panel. Locally that cache is Redis; under tests it is locmem, which keeps each
test process isolated.

`fail_next` / `always_fail` / `set_latency` exist so the chain's failure paths
and the retry button can be rehearsed without breaking anything real.
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from typing import Any

from django.conf import settings
from django.core.cache import cache

from ohc_experience.integrations.naming import APP_NAME_TEMPLATE
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import BridgeCreated
from ohc_experience.integrations.ports import BridgeSpec
from ohc_experience.integrations.ports import BridgeStatus
from ohc_experience.integrations.ports import ClientCreated
from ohc_experience.integrations.ports import ClientSpec
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.ports import GatewayAppCreated
from ohc_experience.integrations.ports import GatewayAppSpec
from ohc_experience.integrations.ports import NotificationMessage
from ohc_experience.integrations.ports import SecretRotated
from ohc_experience.integrations.secret_ref import resolve_secret

logger = logging.getLogger(__name__)

_PREFIX = "local_integrations"
_CONTROL_KEY = f"{_PREFIX}:control"
_TTL = 60 * 60 * 24

# Every key these touch. reset_local_state() clears exactly these, so it cannot
# evict sessions or anything else sharing the cache.
_STORE_KEYS = (
    *(f"{_PREFIX}:{system.value}" for system in ExternalSystem),
    _CONTROL_KEY,
)

DEFAULT_BRIDGE_ACTIVATION_DELAY = 5.0


def _store(system: ExternalSystem) -> dict[str, Any]:
    return cache.get(f"{_PREFIX}:{system.value}", {})


def _save(system: ExternalSystem, data: dict[str, Any]) -> None:
    cache.set(f"{_PREFIX}:{system.value}", data, _TTL)


def _control() -> dict[str, Any]:
    return cache.get(_CONTROL_KEY, {})


def _save_control(data: dict[str, Any]) -> None:
    cache.set(_CONTROL_KEY, data, _TTL)


def fail_next(
    system: ExternalSystem,
    op: str,
    *,
    code: str = "LOCAL_FAILURE",
    retryable: bool = True,
) -> None:
    control = _control()
    control.setdefault("fail_next", {})[f"{system.value}:{op}"] = {
        "code": code,
        "retryable": retryable,
    }
    _save_control(control)


def always_fail(
    system: ExternalSystem,
    *,
    code: str = "LOCAL_UNAVAILABLE",
    retryable: bool = True,
) -> None:
    control = _control()
    control.setdefault("always_fail", {})[system.value] = {
        "code": code,
        "retryable": retryable,
    }
    _save_control(control)


def set_latency(system: ExternalSystem, seconds: float) -> None:
    control = _control()
    control.setdefault("latency", {})[system.value] = seconds
    _save_control(control)


def clear_failures(system: ExternalSystem) -> None:
    control = _control()
    for bucket in ("always_fail", "latency"):
        control.get(bucket, {}).pop(system.value, None)
    control["fail_next"] = {
        key: value
        for key, value in control.get("fail_next", {}).items()
        if not key.startswith(f"{system.value}:")
    }
    _save_control(control)


def reset_local_state() -> None:
    cache.delete_many(list(_STORE_KEYS))


def _guard(system: ExternalSystem, op: str) -> None:
    control = _control()

    latency = control.get("latency", {}).get(system.value, 0.0)
    if latency:
        time.sleep(latency)

    always = control.get("always_fail", {}).get(system.value)
    if always:
        raise AdapterError(
            system,
            always["code"],
            retryable=always["retryable"],
            message=f"{op} failed: always_fail is set",
        )

    pending = control.get("fail_next", {}).pop(f"{system.value}:{op}", None)
    if pending:
        _save_control(control)
        raise AdapterError(
            system,
            pending["code"],
            retryable=pending["retryable"],
            message=f"{op} failed: fail_next was armed",
        )


class LocalIdpAdmin:
    """Client ids are random, mirroring the real adapter's rule."""

    def create_client(self, spec: ClientSpec) -> ClientCreated:
        _guard(ExternalSystem.KEYCLOAK, "create_client")
        # The real adapter resolves each role name with GET /roles/{name}, so an
        # unknown one is a hard 404. Accepting anything here would let a wrong
        # KEYCLOAK_ROLE_NAMES pass every CI run and fail on first contact with a
        # real realm.
        realm_roles = set(settings.LOCAL_KEYCLOAK_REALM_ROLES)
        unknown = sorted(set(spec.role_names) - realm_roles)
        if unknown:
            raise AdapterError(
                ExternalSystem.KEYCLOAK,
                "HTTP_404",
                retryable=False,
                message=f"realm has no role {', '.join(unknown)}",
            )

        clients = _store(ExternalSystem.KEYCLOAK)
        external_id = str(uuid.uuid4())
        client_id = f"SBX_{secrets.token_hex(8).upper()}"
        clients[external_id] = {
            "client_id": client_id,
            "secret": secrets.token_urlsafe(32),
            "roles": list(spec.role_names),
            "enabled": True,
            "reference": spec.reference,
            "display_name": spec.display_name,
        }
        _save(ExternalSystem.KEYCLOAK, clients)
        return ClientCreated(
            client_id=client_id,
            external_id=external_id,
            initial_secret=clients[external_id]["secret"],
        )

    def rotate_client_secret(self, external_id: str) -> SecretRotated:
        _guard(ExternalSystem.KEYCLOAK, "rotate_client_secret")
        clients = _store(ExternalSystem.KEYCLOAK)
        record = clients.get(external_id)
        if record is None:
            raise AdapterError(
                ExternalSystem.KEYCLOAK,
                "HTTP_404",
                retryable=False,
                message=f"no local client {external_id}",
            )
        record["secret"] = secrets.token_urlsafe(32)
        _save(ExternalSystem.KEYCLOAK, clients)
        return SecretRotated(external_id=external_id, secret=record["secret"])

    def disable_client(self, external_id: str) -> None:
        """Idempotent: disabling a missing or already-disabled client succeeds."""
        _guard(ExternalSystem.KEYCLOAK, "disable_client")
        clients = _store(ExternalSystem.KEYCLOAK)
        if external_id in clients:
            clients[external_id]["enabled"] = False
            _save(ExternalSystem.KEYCLOAK, clients)

    def get_client(self, external_id: str) -> dict[str, Any] | None:
        return _store(ExternalSystem.KEYCLOAK).get(external_id)


class LocalApiGateway:
    def create_application(self, spec: GatewayAppSpec) -> GatewayAppCreated:
        """Create-or-lookup on the derived name, as the real adapter does."""
        _guard(ExternalSystem.WSO2, "create_application")
        apps = _store(ExternalSystem.WSO2)
        name = APP_NAME_TEMPLATE.format(reference=spec.reference)

        for existing_id, record in apps.items():
            if record["name"] == name:
                return GatewayAppCreated(external_id=existing_id, name=name)

        external_id = str(uuid.uuid4())
        apps[external_id] = {
            "name": name,
            "reference": spec.reference,
            "subscriptions": sorted(spec.api_ids),
            "keys_mapped": False,
        }
        _save(ExternalSystem.WSO2, apps)
        return GatewayAppCreated(external_id=external_id, name=name)

    def subscribe(self, external_id: str, api_ids: tuple[str, ...]) -> None:
        _guard(ExternalSystem.WSO2, "subscribe")
        apps = _store(ExternalSystem.WSO2)
        record = self._require(apps, external_id)
        record["subscriptions"] = sorted(set(record["subscriptions"]) | set(api_ids))
        _save(ExternalSystem.WSO2, apps)

    def unsubscribe(self, external_id: str, api_ids: tuple[str, ...]) -> None:
        """Idempotent: unsubscribing from what was never subscribed succeeds."""
        _guard(ExternalSystem.WSO2, "unsubscribe")
        apps = _store(ExternalSystem.WSO2)
        record = apps.get(external_id)
        if record is None:
            return
        record["subscriptions"] = sorted(set(record["subscriptions"]) - set(api_ids))
        _save(ExternalSystem.WSO2, apps)

    def map_keys(self, external_id: str, consumer_key: str, secret_ref: str) -> None:
        _guard(ExternalSystem.WSO2, "map_keys")
        apps = _store(ExternalSystem.WSO2)
        record = self._require(apps, external_id)
        # Dereferenced and thrown away, as the real adapter does. A stand-in that
        # accepting a dead ref is what would let a secret-expiry dead-end hide.
        resolve_secret(secret_ref, ExternalSystem.WSO2)
        record["keys_mapped"] = True
        record["consumer_key"] = consumer_key
        record["secret_ref"] = secret_ref  # a reference, never a secret value
        _save(ExternalSystem.WSO2, apps)

    def get_application(self, external_id: str) -> dict[str, Any] | None:
        return _store(ExternalSystem.WSO2).get(external_id)

    @staticmethod
    def _require(apps: dict[str, Any], external_id: str) -> dict[str, Any]:
        record = apps.get(external_id)
        if record is None:
            raise AdapterError(
                ExternalSystem.WSO2,
                "HTTP_404",
                retryable=False,
                message=f"no local application {external_id}",
            )
        return record


class LocalBridgeRegistry:
    """A pending -> active delay, so polling has something to poll."""

    def create_bridge(self, spec: BridgeSpec) -> BridgeCreated:
        _guard(ExternalSystem.HIECM, "create_bridge")
        bridges = _store(ExternalSystem.HIECM)
        bridges[spec.bridge_id] = {
            "name": spec.name,
            "url": spec.url,
            "active_from": time.time() + self._activation_delay(),
            "deactivated": False,
        }
        _save(ExternalSystem.HIECM, bridges)
        return BridgeCreated(bridge_id=spec.bridge_id)

    def get_bridge_status(self, bridge_id: str) -> BridgeStatus:
        _guard(ExternalSystem.HIECM, "get_bridge_status")
        record = _store(ExternalSystem.HIECM).get(bridge_id)
        if record is None:
            raise AdapterError(
                ExternalSystem.HIECM,
                "HTTP_404",
                retryable=False,
                message=f"no local bridge {bridge_id}",
            )
        active = not record["deactivated"] and time.time() >= record["active_from"]
        return BridgeStatus(bridge_id=bridge_id, active=active)

    def deactivate_bridge(self, bridge_id: str) -> None:
        """Idempotent: deactivating a missing or dead bridge succeeds."""
        _guard(ExternalSystem.HIECM, "deactivate_bridge")
        bridges = _store(ExternalSystem.HIECM)
        if bridge_id in bridges:
            bridges[bridge_id]["deactivated"] = True
            _save(ExternalSystem.HIECM, bridges)

    @staticmethod
    def _activation_delay() -> float:
        return float(
            getattr(
                settings,
                "LOCAL_BRIDGE_ACTIVATION_DELAY_SECONDS",
                DEFAULT_BRIDGE_ACTIVATION_DELAY,
            ),
        )


class LocalNotificationGateway:
    """Keeps each message instead of sending it. Under DEBUG it also logs the
    values, so a verification code can be read off the runserver console."""

    def send(self, message: NotificationMessage) -> None:
        _guard(ExternalSystem.NOTIFICATION, "send")
        template = message.template
        store = _store(ExternalSystem.NOTIFICATION)
        store.setdefault("sent", []).append(
            {
                "channel": template.channel.value,
                "receiver": message.receiver,
                "template_id": template.id,
                "subject": template.subject,
                "values": list(message.values),
                "content_type": template.content_type.value,
            },
        )
        _save(ExternalSystem.NOTIFICATION, store)
        if settings.DEBUG:
            logger.info(
                "%s to %s: %s",
                template.subject,
                message.receiver,
                ", ".join(message.values),
            )

    def sent(self) -> list[dict[str, Any]]:
        return _store(ExternalSystem.NOTIFICATION).get("sent", [])
