from django import template

register = template.Library()


@register.filter
def readable(value):
    return str(value).replace("_", " ").capitalize()


@register.filter
def sections(form):
    groups = {
        "OrganisationForm": [
            (
                "Identity",
                ["name", "description", "entity_type", "category", "website", "logo"],
            ),
            (
                "Registered address",
                ["registered_address", "pincode", "state", "district"],
            ),
            (
                "Verification document",
                [
                    "verification_document_type",
                    "verification_document_number",
                    "supporting_document",
                ],
            ),
        ],
        "ProductRegistrationForm": [
            ("Product details", ["name", "description", "category", "solution_type"]),
            ("Tracks and milestones", ["applied_milestones"]),
        ],
        "ExitEvidenceForm": [
            ("Sandbox testing", ["start_date", "end_date", "tentative_demo_date"]),
            ("WASA audit", ["wasa_agency", "wasa_date"]),
            (
                "Functional testing",
                ["functional_certificate", "functional_report", "supporting_evidence"],
            ),
        ],
    }
    return [
        {"title": title, "fields": [form[key] for key in keys if key in form.fields]}
        for title, keys in groups.get(type(form).__name__, [("", list(form.fields))])
    ]


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
