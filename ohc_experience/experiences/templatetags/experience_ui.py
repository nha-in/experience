import datetime
import json
import re
from urllib.parse import urlencode

from django import template
from django.urls import reverse
from django.utils.formats import date_format

from ohc_experience.experiences import workflows
from ohc_experience.experiences.registry import registry
from ohc_experience.experiences.workflows import QUERY_TARGETS

register = template.Library()


@register.filter
def event_url(event, product):
    """An event's page, staying inside the product it was reached from."""
    if product:
        return reverse("experiences:product-event", args=[product.reference, event.pk])
    return event.get_absolute_url()


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


def _label_query(query, labels):
    """Name the field a query asks about as the form it was asked of labels it."""
    if query.submission_id not in labels:
        labels[query.submission_id] = {
            field["key"]: field["label"] for field in query.submission.field_schema
        }
    query.field_label = QUERY_TARGETS.get(query.field_key) or labels[
        query.submission_id
    ].get(query.field_key, readable(query.field_key))
    return query


@register.simple_tag
def open_queries(item, snapshot=None):
    """Questions on this submission still waiting for the integrator's reply.

    Only a request under review takes replies, and only on the submission they
    were asked of, so these are the open queries the queries card keeps in play.
    """
    if not item or not item.pending:
        return []
    snapshot_id = snapshot.pk if snapshot else item.selected_submission_id
    if not snapshot_id or snapshot_id != item.selected_submission_id:
        return []
    labels = {}
    return [
        _label_query(query, labels)
        for query in item.queries.filter(
            submission_id=snapshot_id,
            status="open",
        ).select_related("submission", "raised_by")
    ]


@register.simple_tag
def earlier_queries(item, snapshot=None):
    """Queries left unresolved on the version the integrator took back to edit.

    The form being edited marks them, and so does the version that replaces it
    while that is under review. Given a `snapshot`, only the version under
    review marks them: a version shown on its own is read as it was sent.
    """
    if not item:
        return []
    if snapshot and (not item.pending or snapshot.pk != item.selected_submission_id):
        return []
    labels = {}
    return [_label_query(query, labels) for query in workflows.earlier_queries(item)]


@register.simple_tag
def queries_on(queries, key):
    """Those of these queries about one field."""
    return [query for query in queries if query.field_key == key]


@register.simple_tag
def unplaced_queries(queries, form):
    """Those of these queries no field on the form can carry: the whole form,
    or a field the form no longer asks."""
    if not queries:
        return []
    shown = {
        field.name
        for section in sections(form)
        for field in section["fields"]
        if not field.is_hidden
    }
    return [query for query in queries if query.field_key not in shown]


def _attached(submission, key):
    """A version's files for one field, by where they are stored."""
    return sorted(
        submission.attachments.filter(field_key=key, is_current=True).values_list(
            "file",
            flat=True,
        ),
    )


@register.filter
def one_per_field(queries):
    """The first of these queries on each field, in the order they were raised."""
    first = {}
    for query in queries:
        first.setdefault(query.field_key, query)
    return list(first.values())


@register.simple_tag
def snapshot_rows(snapshot, item=None, drafts=None):
    """The submitted answers, one row per field.

    `drafts` holds the reviewer's open question boxes by field key, so a row's
    `draft` is its question so far, or None while its box is closed. A row
    queried on the version this one replaced lists those queries in `earlier`,
    and `changed` says whether its answer differs from that version's.
    """
    if not snapshot:
        return []
    attachments = {}
    for attachment in snapshot.attachments.filter(is_current=True):
        attachments.setdefault(attachment.field_key, []).append(attachment)
    asked = {}
    for query in open_queries(item, snapshot):
        asked.setdefault(query.field_key, []).append(query)
    earlier = {}
    for query in earlier_queries(item, snapshot):
        earlier.setdefault(query.field_key, []).append(query)
    warnings = registry.get_form(snapshot.form_key).answer_warnings(snapshot.data)
    rows = []
    for field in snapshot.field_schema:
        key = field["key"]
        file_field = "File" in field["type"] or "Image" in field["type"]
        changed = None
        if key in earlier:
            before = earlier[key][0].submission
            changed = (
                _attached(before, key)
                != sorted(
                    attachment.file.name for attachment in attachments.get(key, [])
                )
                if file_field
                else before.data.get(key) != snapshot.data.get(key)
            )
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
                "file_field": file_field,
                # Only http(s): imported answers never passed URLField's check.
                "link": value
                if field["type"] == "URLField"
                and str(value).startswith(("http://", "https://"))
                else None,
                "queries": asked.get(key, []),
                "query_open": key in asked,
                "earlier": earlier.get(key, []),
                "changed": changed,
                "draft": (drafts or {}).get(key),
                "warning": warnings.get(key),
            },
        )
    return rows


@register.simple_tag
def query_form_id(item):
    """The id of the form that sends this request's queries on the product page."""
    return f"query-form-{item.pk}"


@register.simple_tag
def query_url(item, field_key):
    """Without scripts, a Query link reloads the product page with the box open."""
    path = reverse("experiences:product-detail", args=[item.review_product.reference])
    query = urlencode({"review": item.pk, "action": "query", "field": field_key})
    return f"{path}?{query}#review-{item.pk}"


@register.simple_tag
def draft_for(drafts, key):
    """A question box's text so far, or None while it is closed."""
    return (drafts or {}).get(key)


@register.simple_tag
def query_groups(item, *, can_resolve=False, can_reply=False):
    """A review's queries, sorted by whether the person looking can act on them.

    An answered query is theirs to act on when they may resolve it, and an
    open one when they may reply. Those open in full; the other queries still
    in play fold to a line each, and whoever may resolve can close an open one
    from there. The queries left on the version the integrator took back to
    edit fold too, apart from the settled ones, because the form marks them
    and leads here. Resolved queries, those asked of an earlier
    submission and any left on a request no longer under review are settled.
    `answered` and `open` count the queries still in play. Each query is
    labelled as its submission's form labels the field.
    """
    groups = {
        "actionable": [],
        "waiting": [],
        "earlier": [],
        "settled": [],
        "answered": 0,
        "open": 0,
    }
    labels = {}
    earlier = {query.pk for query in workflows.earlier_queries(item)}
    for query in item.queries.select_related("submission", "raised_by", "replied_by"):
        _label_query(query, labels)
        query.earlier_submission = query.submission_id != item.selected_submission_id
        query.earlier_version = query.pk in earlier
        query.live = (
            item.pending and not query.earlier_submission and query.status != "resolved"
        )
        # A folded line quotes the reply when there is one still to check.
        query.excerpt = (
            query.reply if query.live and query.status == "answered" else query.question
        )
        if not query.live:
            groups["earlier" if query.earlier_version else "settled"].append(query)
            continue
        groups[query.status] += 1
        query.can_close = can_resolve and query.status == "open"
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


def _table_query(request, **changes):
    """The current query string with `changes` applied; None drops a key."""
    query = request.GET.copy()
    for key in ("page", "export", "table"):
        query.pop(key, None)
    for key, value in changes.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    return f"?{query.urlencode()}"


@register.inclusion_tag("components/sort_header.html", takes_context=True)
def sort_header(  # noqa: PLR0913
    context,
    label,
    key,
    css="",
    *,
    note="",
    descending_first=False,
    param="sort",
    current=None,
):
    """A column heading that sorts its table, toggling between directions.

    Dates read best newest first, so their first click sorts descending. A
    second table on a page names its own query `param` and the `current` sort
    it is in, where the page's main table reads `table_sort`.
    """
    if current is None:
        current = context.get("table_sort", "")
    if current == key:
        state, target = "ascending", f"-{key}"
    elif current == f"-{key}":
        state, target = "descending", key
    else:
        state, target = "", f"-{key}" if descending_first else key
    return {
        "label": label,
        "note": note,
        "css": css,
        "state": state,
        "href": _table_query(context["request"], **{param: target}),
    }


@register.simple_tag(takes_context=True)
def reload_url(context, key):
    """This page without its page number or `key`, for a dropdown that sends
    `key` back itself, as the page-size one does.

    A bare "?" would come back as "?&per_page=" once htmx adds the value.
    """
    request = context["request"]
    query = request.GET.copy()
    for name in ("page", "export", key):
        query.pop(name, None)
    encoded = query.urlencode()
    return f"{request.path}?{encoded}" if encoded else request.path


@register.simple_tag(takes_context=True)
def export_url(context, export, table=None):
    """This table's rows as an Excel file, filtered and sorted as on screen.

    `table` names which one, on a page with two.
    """
    return _table_query(
        context["request"],
        export=export,
        per_page=None,
        table=table or None,
    )
