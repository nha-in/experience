from django import forms

from .wasa_extraction import is_enabled


class WasaCertificateInput(forms.FileInput):
    """A PDF chooser that offers to read the audit fields off the certificate.

    Without a configured model the hook's attribute is left off, so the script
    finds nothing to attach to and the field behaves like any other upload.
    """

    template_name = "abdm/widgets/wasa_certificate.html"

    def __init__(self, attrs=None):
        super().__init__({"accept": ".pdf", **(attrs or {})})

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        widget = context["widget"]
        if is_enabled():
            widget["attrs"]["data-read-document"] = name
        widget["read_status_id"] = f"{widget['attrs'].get('id', name)}_reading"
        return context

    class Media:
        js = ("js/document-reader.js",)
