import json

from django import template

register = template.Library()


@register.filter
def readable(value):
    return str(value).replace("_", " ").capitalize()


@register.filter
def outcome_rows(outcome):
    schema = outcome.field_schema or [
        {"key": key, "label": readable(key)} for key in outcome.data
    ]
    rows = []
    for field in schema:
        value = outcome.data.get(field["key"])
        if value is None or value == "":
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        rows.append((field.get("label", readable(field["key"])), value))
    return rows


@register.filter
def sections(form):
    rules = getattr(form, "conditional_sections", {})
    return [
        {
            "title": title,
            "fields": [form[key] for key in keys if key in form.fields],
            "note": getattr(form, "section_notes", {}).get(title, ""),
            "badges": getattr(form, "section_badges", {}).get(title, ()),
            "show_when": _rule(form, rules.get(title)),
        }
        for title, keys in getattr(form, "sections", [("", list(form.fields))])
    ]


def _rule(form, rule):
    """Resolve a (controlling field, value) rule against what is answered now."""
    if not rule:
        return None
    controller, value = rule
    current = form[controller].value() or []
    if isinstance(current, str):
        current = [current]
    return {"field": controller, "value": value, "active": value in current}


@register.filter
def show_when(form, name):
    return _rule(form, getattr(form, "conditional_fields", {}).get(name))


@register.simple_tag
def snapshot_rows(snapshot, item=None):
    if not snapshot:
        return []
    attachments = {}
    for attachment in snapshot.attachments.filter(is_current=True):
        attachments.setdefault(attachment.field_key, []).append(attachment)
    open_fields = (
        set(
            item.queries.filter(submission=snapshot, status="open").values_list(
                "field_key",
                flat=True,
            ),
        )
        if item
        else set()
    )
    rows = []
    for field in snapshot.field_schema:
        key = field["key"]
        value = snapshot.data.get(key)
        choices = {
            str(choice["value"]): choice["label"] for choice in field.get("choices", [])
        }
        if isinstance(value, list):
            value = ", ".join(choices.get(str(entry), str(entry)) for entry in value)
        elif value is not None:
            value = choices.get(str(value), str(value))
        rows.append(
            {
                "key": key,
                "label": field["label"],
                "value": value,
                "files": attachments.get(key, []),
                "file_field": "File" in field["type"] or "Image" in field["type"],
                "query_open": key in open_fields,
            },
        )
    return rows


@register.filter
def page_numbers(page):
    """Five page numbers around the current one, flagging the three kept on phones."""
    narrow = _window(page.number, page.paginator.num_pages, 3)
    return [
        (number, number in narrow)
        for number in _window(page.number, page.paginator.num_pages, 5)
    ]


def _window(number, num_pages, size):
    start = max(1, min(number - size // 2, num_pages - size + 1))
    return range(start, min(num_pages, start + size - 1) + 1)
