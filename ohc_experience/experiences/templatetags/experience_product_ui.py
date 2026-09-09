"""Read-only product presentation derived from the active form definition."""

from django import forms
from django import template

register = template.Library()


@register.simple_tag
def evidence_readiness(form):
    """Summarise saved evidence without binding UI to a programme's field names."""
    rows = []
    required_uploads = getattr(form, "required_uploads", ())
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
        rows.append(
            {
                "label": form[key].label,
                "field_id": form[key].auto_id,
                "done": bool(done),
            },
        )
    completed = sum(row["done"] for row in rows)
    total = len(rows)
    return {
        "rows": rows,
        "completed": completed,
        "total": total,
        "missing": total - completed,
        "arc": round(238.76 * completed / total) if total else 0,
    }
