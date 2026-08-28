from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured

if TYPE_CHECKING:
    from .definitions import ApplicationDefinition


class ExperienceRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, type[ApplicationDefinition]] = {}

    def register(
        self,
        definition: type[ApplicationDefinition],
    ) -> type[ApplicationDefinition]:
        definition.validate()
        if definition.key in self._definitions:
            msg = f"An experience named {definition.key!r} is already registered."
            raise ImproperlyConfigured(msg)
        self._definitions[definition.key] = definition
        return definition

    def get(self, key: str) -> type[ApplicationDefinition]:
        try:
            return self._definitions[key]
        except KeyError as exc:
            msg = f"Unknown experience definition: {key!r}"
            raise ImproperlyConfigured(msg) from exc

    def all(self) -> tuple[type[ApplicationDefinition], ...]:
        return tuple(self._definitions.values())


registry = ExperienceRegistry()
