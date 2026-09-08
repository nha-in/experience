from typing import ClassVar

from django.core.exceptions import ImproperlyConfigured

from .models import FormReuseScope


class ApplicationFormDefinition:
    """An independent form identity used when an application is created."""

    key: ClassVar[str]
    name: ClassVar[str]
    reuse_scope: ClassVar[str] = FormReuseScope.PRODUCT
    schema_version: ClassVar[int] = 1


class ApplicationDefinition:
    """The application types supported by the current portal."""

    key: ClassVar[str]
    name: ClassVar[str]
    reference_prefix: ClassVar[str] = "APP"
    initial_status: ClassVar[str] = "draft"
    forms: ClassVar[tuple[type[ApplicationFormDefinition], ...]]

    @classmethod
    def validate(cls) -> None:
        keys = [form.key for form in cls.forms]
        if not cls.key or not keys or len(keys) != len(set(keys)):
            msg = "Applications require a key and uniquely named forms."
            raise ImproperlyConfigured(msg)
        for form in cls.forms:
            if not form.key or form.reuse_scope not in FormReuseScope.values:
                msg = "Each form requires a key and a supported reuse scope."
                raise ImproperlyConfigured(msg)
