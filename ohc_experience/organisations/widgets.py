from django import forms
from django.urls import reverse_lazy

from . import states


class WebsiteInput(forms.TextInput):
    """A web address box that also takes a bare domain.

    A `type="url"` box makes the browser refuse anything without a scheme, so
    someone who types `acme.in` is stopped before the form is ever sent, with
    only the browser's own "Enter a URL" to go on. A text box lets it through,
    and `URLField` puts the `https://` back on the way in. The placeholder
    shows the full shape so the address stays the obvious thing to paste.
    """

    def __init__(self, attrs=None):
        default_attrs = {
            "inputmode": "url",
            "autocomplete": "url",
            "autocapitalize": "none",
            "spellcheck": "false",
            "placeholder": "https://example.com",
        }
        super().__init__({**default_attrs, **(attrs or {})})


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
        base = widget["attrs"].get("id", name)
        status_id = f"{base}_lookup_status"
        described_by = widget["attrs"].get("aria-describedby", "").split()
        if status_id not in described_by:
            described_by.append(status_id)
        widget["attrs"]["aria-describedby"] = " ".join(described_by)
        widget["lookup_status_id"] = status_id
        # Carried in the page so a failed lookup can still offer the names,
        # including when the browser is what cannot reach the server.
        locations_id = f"{base}_offline_locations"
        widget["attrs"]["data-offline-locations"] = locations_id
        widget["offline_locations_id"] = locations_id
        widget["offline_locations"] = states.as_choices()
        return context

    class Media:
        js = ("js/pincode-lookup.js",)
