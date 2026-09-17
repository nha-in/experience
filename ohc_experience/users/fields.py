import re

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

INDIA = "+91"
INDIAN_MOBILE_NUMBER = re.compile(r"(?:\+?91|0)?([6-9]\d{9})")
SEPARATORS = re.compile(r"[\s().-]")


class MobileNumberField(forms.CharField):
    """An Indian mobile number, cleaned to +91 and its ten digits."""

    default_error_messages = {
        "invalid": _("Enter a 10-digit Indian mobile number."),
    }

    def __init__(self, **kwargs):
        kwargs.setdefault("label", _("Mobile number"))
        kwargs.setdefault(
            "widget",
            forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
        )
        super().__init__(**kwargs)

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return value
        match = INDIAN_MOBILE_NUMBER.fullmatch(SEPARATORS.sub("", value))
        if match is None:
            raise ValidationError(self.error_messages["invalid"], code="invalid")
        return f"{INDIA}{match.group(1)}"
