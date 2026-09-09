"""Where the portal's saved files are downloaded from.

The upload plumbing itself (validation, size limits, the existing-file rows
the upload widget draws) lives in ``experiences.uploads``. This module adds
the one thing it cannot know: the URL. Downloads never expose a storage path;
``document_url`` points at the permission-checked ``products:document`` view
and ``PortalFileFormMixin`` wires it into a ModelForm's existing-file rows.
"""

from __future__ import annotations

from django.urls import reverse

from ohc_experience.experiences.uploads import ModelFileFormMixin


def document_url(kind: str, pk: int, field: str) -> str:
    return reverse("products:document", kwargs={"kind": kind, "pk": pk, "field": field})


class PortalFileFormMixin(ModelFileFormMixin):
    """A ModelForm whose saved files are served by the portal's download view."""

    download_kind = ""

    def download_url(self, field_name: str) -> str:
        return document_url(self.download_kind, self.instance.pk, field_name)
