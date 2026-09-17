"""Read-only product presentation derived from the active form definition."""

from django import forms
from django import template

register = template.Library()


@register.simple_tag
def evidence_readiness(form):
    """Summarise saved evidence without binding UI to a programme's field names."""
    required_uploads = getattr(form, "required_uploads", ())
    field_states = {}
    for key, field in form.fields.items():
        declared_field = form.base_fields.get(key, field)
        if not declared_field.required and key not in required_uploads:
            continue
        if isinstance(field, forms.FileField):
            done = bool(getattr(form, "existing_files", {}).get(key))
        else:
            value = form.initial.get(key)
            done = (
                field.to_python(value)
                if isinstance(field, forms.BooleanField)
                else value not in (None, "", [], ())
            )
        field_states[key] = {"field_id": form[key].auto_id, "done": bool(done)}

    rows = []
    sections = getattr(form, "sections", ())
    for title, keys in sections:
        fields = [field_states[key] for key in keys if key in field_states]
        if not fields:
            continue
        missing = next((field for field in fields if not field["done"]), None)
        rows.append(
            {
                "label": title,
                "field_id": (missing or fields[0])["field_id"],
                "field_ids": [field["field_id"] for field in fields],
                "done": missing is None,
            },
        )
    if not sections:
        rows = [
            {
                "label": form[key].label,
                "field_id": state["field_id"],
                "field_ids": [state["field_id"]],
                "done": state["done"],
            }
            for key, state in field_states.items()
        ]
    completed = sum(row["done"] for row in rows)
    total = len(rows)
    return {
        "rows": rows,
        "completed": completed,
        "total": total,
        "missing": total - completed,
        "first_missing": next((row for row in rows if not row["done"]), None),
        "arc": round(238.76 * completed / total) if total else 0,
    }
