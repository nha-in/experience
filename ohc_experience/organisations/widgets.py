from django import forms
from django.urls import reverse_lazy


class PincodeInput(forms.TextInput):
    template_name = "organisations/widgets/pincode.html"

    def __init__(self, attrs=None):
        default_attrs = {
            "data-pincode-lookup": reverse_lazy("organisations:pincode-lookup"),
            "inputmode": "numeric",
            "autocomplete": "postal-code",
            "maxlength": 6,
        }
        super().__init__({**default_attrs, **(attrs or {})})

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        widget = context["widget"]
        status_id = f"{widget['attrs'].get('id', name)}_lookup_status"
        described_by = widget["attrs"].get("aria-describedby", "").split()
        if status_id not in described_by:
            described_by.append(status_id)
        widget["attrs"]["aria-describedby"] = " ".join(described_by)
        widget["lookup_status_id"] = status_id
        return context

    class Media:
        js = ("js/pincode-lookup.js",)
