"""Template helpers for rendering forms in the careui idiom.

Django widgets carry their own attrs, and allauth builds a dozen forms we do not
own. Rather than subclass every one of them, these helpers stamp the right
classes onto a bound field at render time, so `{% ui_field form.email %}` looks
the same whoever built the form.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django import forms
from django import template

if TYPE_CHECKING:
    from django.forms import BoundField

register = template.Library()

# Widget class → the careui control class it should render as.
_CONTROL_CLASSES: list[tuple[type, str]] = [
    (forms.CheckboxInput, "ui-checkbox"),
    (forms.CheckboxSelectMultiple, ""),
    (forms.RadioSelect, ""),
    (forms.Textarea, "ui-textarea"),
    (forms.SelectMultiple, "ui-select"),
    (forms.Select, "ui-select"),
]
_DEFAULT_CONTROL_CLASS = "ui-input"

# Widgets that render as a native <select> and therefore need the chevron wrapper.
_SELECT_WIDGETS = (forms.Select, forms.SelectMultiple)


def _control_class(widget: forms.Widget) -> str:
    for widget_type, css_class in _CONTROL_CLASSES:
        if isinstance(widget, widget_type):
            return css_class
    return _DEFAULT_CONTROL_CLASS


def _style(field: BoundField, extra_class: str = "") -> BoundField:
    """Merge careui classes and validation state into the widget's attrs."""
    widget = field.field.widget
    attrs = widget.attrs
    classes = [
        _control_class(widget),
        attrs.get("class", ""),
        extra_class,
    ]
    merged = " ".join(part for part in classes if part).strip()
    if merged:
        attrs["class"] = merged
    if field.errors:
        attrs["aria-invalid"] = "true"
    if field.help_text and "aria-describedby" not in attrs:
        attrs["aria-describedby"] = f"{field.auto_id}_helptext"
    return field


@register.simple_tag
def ui_widget(field: BoundField, extra_class: str = "") -> str:
    """Render just the control, already carrying its careui classes."""
    return _style(field, extra_class).as_widget()


@register.filter
def is_checkbox(field: BoundField) -> bool:
    return isinstance(field.field.widget, forms.CheckboxInput)


@register.filter
def is_select(field: BoundField) -> bool:
    return isinstance(field.field.widget, _SELECT_WIDGETS)


@register.inclusion_tag("components/form_field.html")
def ui_field(
    field: BoundField,
    label: str = "",
    placeholder: str = "",
    help_text: str = "",
    extra_class: str = "",
) -> dict:
    """Render a full label + control + help + errors block.

    `label`, `placeholder` and `help_text` override whatever the form declared,
    which is how the allauth screens pick up the mockup's copy without us
    reaching into allauth's form classes.
    """
    if placeholder:
        field.field.widget.attrs["placeholder"] = placeholder
    return {
        "field": _style(field, extra_class),
        "label": label or field.label,
        "help_text": help_text or field.help_text,
        # The mockups mark nothing as required — almost every field is. Flag the
        # exceptions instead, which is both closer to the design and the clearer
        # convention.
        "is_optional": not field.field.required,
    }


@register.simple_tag
def ui_non_field_errors(form) -> dict:
    return form.non_field_errors()
