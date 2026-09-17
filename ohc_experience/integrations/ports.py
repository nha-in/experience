"""Ports: the only vocabulary domain code uses to reach an external system.

Protocols and DTOs live here so domain apps never import httpx, an adapter
module, or an external system's JSON shapes.

Signatures take DTOs rather than domain models on purpose: a port that imported
`Product` would point the dependency arrow back at the domain and defeat the
anti-corruption layer.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from dataclasses import field
from typing import Protocol


class ExternalSystem(enum.StrEnum):
    """Systems we call: the three we provision into, and ABDM notifications."""

    KEYCLOAK = "KEYCLOAK"
    WSO2 = "WSO2"
    HIECM = "HIECM"
    NOTIFICATION = "NOTIFICATION"


class AdapterError(Exception):
    """The only exception an adapter may raise.

    An unexpected response shape must be mapped to this rather than allowed to
    surface as httpx/JSON/KeyError noise. `retryable` tells the caller whether a
    later attempt could plausibly succeed; the provisioning chain uses it to
    decide between retrying and failing the run.
    """

    def __init__(
        self,
        system: ExternalSystem,
        code: str,
        *,
        retryable: bool,
        message: str = "",
    ) -> None:
        self.system = system
        self.code = code
        self.retryable = retryable
        self.message = message
        super().__init__(
            f"[{system}] {code}: {message}" if message else f"[{system}] {code}",
        )


@dataclass(frozen=True, slots=True)
class ClientSpec:
    reference: str
    display_name: str
    #: Names, never realm UUIDs — those pin the config to one Keycloak instance.
    role_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClientCreated:
    client_id: str
    external_id: str
    # repr=False: this value reaches the user once and is never persisted or logged.
    initial_secret: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SecretRotated:
    external_id: str
    secret: str = field(repr=False)


class IdpAdmin(Protocol):
    """Keycloak client lifecycle."""

    def create_client(self, spec: ClientSpec) -> ClientCreated: ...

    def rotate_client_secret(self, external_id: str) -> SecretRotated: ...

    def disable_client(self, external_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class GatewayAppSpec:
    reference: str
    name: str
    api_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GatewayAppCreated:
    external_id: str
    name: str


class ApiGateway(Protocol):
    """WSO2 application, subscriptions and key mapping."""

    def create_application(self, spec: GatewayAppSpec) -> GatewayAppCreated: ...

    def subscribe(self, external_id: str, api_ids: tuple[str, ...]) -> None: ...

    def map_keys(
        self,
        external_id: str,
        consumer_key: str,
        secret_ref: str,
    ) -> None: ...

    def unsubscribe(self, external_id: str, api_ids: tuple[str, ...]) -> None: ...


@dataclass(frozen=True, slots=True)
class BridgeSpec:
    bridge_id: str
    name: str
    url: str
    entity: str


@dataclass(frozen=True, slots=True)
class BridgeCreated:
    bridge_id: str


@dataclass(frozen=True, slots=True)
class BridgeStatus:
    bridge_id: str
    active: bool


class BridgeRegistry(Protocol):
    """HIE-CM bridge lifecycle."""

    def create_bridge(self, spec: BridgeSpec) -> BridgeCreated: ...

    def get_bridge_status(self, bridge_id: str) -> BridgeStatus: ...

    def deactivate_bridge(self, bridge_id: str) -> None: ...


class NotificationChannel(enum.StrEnum):
    EMAIL = "email"
    SMS = "sms"


class NotificationContentType(enum.StrEnum):
    OTP = "otp"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    """An approved template and the values for its `{0}`, `{1}`… placeholders."""

    channel: NotificationChannel
    receiver: str
    template_id: str
    subject: str
    # repr=False: the values carry one-time codes.
    values: tuple[str, ...] = field(repr=False)
    content_type: NotificationContentType = NotificationContentType.INFO


class NotificationGateway(Protocol):
    """ABDM's notification service, which only delivers approved templates."""

    def send(self, message: NotificationMessage) -> None: ...
