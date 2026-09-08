from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

if TYPE_CHECKING:
    from .definitions import ApplicationDefinition


class ExperienceRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, type[ApplicationDefinition]] = {}
        self._forms = {}
        self._programs = {}

    def register(
        self,
        definition: type[ApplicationDefinition],
    ) -> type[ApplicationDefinition]:
        definition.validate()
        if definition.key in self._definitions:
            msg = f"An experience named {definition.key!r} is already registered."
            raise ImproperlyConfigured(msg)
        self._definitions[definition.key] = definition
        for form in definition.forms:
            self.register_form(form)
        return definition

    def register_form(self, definition):
        previous = self._forms.get(definition.key)
        if previous is not None and previous is not definition:
            msg = f"Form {definition.key!r} is already registered."
            raise ImproperlyConfigured(msg)
        self._forms[definition.key] = definition
        return definition

    def get_form(self, key):
        try:
            return self._forms[key]
        except KeyError as error:
            msg = f"Unknown form definition: {key!r}"
            raise ImproperlyConfigured(msg) from error

    def register_program(self, program):
        program.validate()
        if program.key in self._programs:
            msg = f"Program {program.key!r} is already registered."
            raise ImproperlyConfigured(msg)
        self.register_form(program.organisation_form)
        for definition in (program.product_application, program.milestone_application):
            self.register(definition)
        self._programs[program.key] = program
        return program

    def get_program(self, key):
        try:
            return self._programs[key]
        except KeyError as error:
            msg = f"Unknown program definition: {key!r}"
            raise ImproperlyConfigured(msg) from error

    def get(self, key: str) -> type[ApplicationDefinition]:
        try:
            return self._definitions[key]
        except KeyError as exc:
            msg = f"Unknown experience definition: {key!r}"
            raise ImproperlyConfigured(msg) from exc

    def all(self) -> tuple[type[ApplicationDefinition], ...]:
        return tuple(self._definitions.values())


registry = ExperienceRegistry()


def get_program(key=None):
    return registry.get_program(key or settings.EXPERIENCE_PORTAL)


def load_definitions():
    for path in settings.EXPERIENCE_IMPLEMENTATIONS:
        registry.register_program(import_string(path))
