"""Small template helpers for the portal's screens."""

from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def dict_get(mapping, key):
    """`{{ mapping|dict_get:key }}` — a dictionary lookup by a variable key."""
    if mapping is None:
        return ""
    return mapping.get(key, "")


@register.filter
def pct_of(value, maximum) -> int:
    """`{{ count|pct_of:max }}` — a bar width, 0-100, safe when max is 0."""
    try:
        value, maximum = int(value), int(maximum)
    except TypeError, ValueError:
        return 0
    if maximum <= 0 or value <= 0:
        return 0
    return min(100, round(value / maximum * 100))


@register.filter
def file_basename(value) -> str:
    """`{{ upload.name|file_basename }}` — the stored name without its path."""
    return str(value).rsplit("/", 1)[-1] if value else ""
