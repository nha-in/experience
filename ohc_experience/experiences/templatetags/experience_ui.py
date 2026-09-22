import datetime
import json
import re

from django import template
from django.utils.formats import date_format

register = template.Library()

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _answer(value):
    # Answers are stored as JSON, so a date comes back as its ISO string. Print
    # it day first, like every other date.
    if isinstance(value, str) and ISO_DATE.fullmatch(value):
        try:
            return date_format(datetime.date.fromisoformat(value), "d/m/Y")
        except ValueError:
            pass
    return value


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
        rows.append((field.get("label", readable(field["key"])), _answer(value)))
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
            value = choices.get(str(value), _answer(str(value)))
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


@register.simple_tag
def query_groups(item, *, can_resolve=False, can_reply=False):
    """A review's queries, sorted by whether the person looking can act on them.

    An answered query is theirs to act on when they may resolve it, and an
    open one when they may reply. Those open in full; the other queries still
    in play fold to a line each. Resolved queries, those asked of an earlier
    submission and any left on a request no longer under review are settled.
    `answered` and `open` count the queries still in play. Each query is
    labelled as its submission's form labels the field.
    """
    groups = {"actionable": [], "waiting": [], "settled": [], "answered": 0, "open": 0}
    labels = {}
    for query in item.queries.select_related("submission", "raised_by", "replied_by"):
        if query.submission_id not in labels:
            labels[query.submission_id] = {
                field["key"]: field["label"] for field in query.submission.field_schema
            }
        query.field_label = (
            "Whole form"
            if query.field_key == "form"
            else labels[query.submission_id].get(
                query.field_key,
                readable(query.field_key),
            )
        )
        query.earlier_submission = query.submission_id != item.selected_submission_id
        query.live = (
            item.pending and not query.earlier_submission and query.status != "resolved"
        )
        # A folded line quotes the reply when there is one still to check.
        query.excerpt = (
            query.reply if query.live and query.status == "answered" else query.question
        )
        if not query.live:
            groups["settled"].append(query)
            continue
        groups[query.status] += 1
        can_act = can_resolve if query.status == "answered" else can_reply
        groups["actionable" if can_act else "waiting"].append(query)
    # Replies to check come before questions still waiting for one.
    for name in ("actionable", "waiting"):
        groups[name].sort(key=lambda query: query.status != "answered")
    return groups


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
