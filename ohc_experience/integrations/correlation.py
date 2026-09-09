"""One id tying a request to the chain it enqueued and the calls that chain made."""

from __future__ import annotations

import contextvars
import uuid

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id",
    default="",
)


def get_correlation_id() -> str:
    current = _correlation_id.get()
    if not current:
        current = uuid.uuid4().hex
        _correlation_id.set(current)
    return current


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)
