"""The track page's Documents view: every file saved across its milestones, in
one place."""

import re

import pytest
from django.urls import reverse

from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import workflows

pytestmark = pytest.mark.django_db


def page(client, environment, **params):  # noqa: F811
    url = reverse(
        "experiences:track",
        args=[environment["workspace"].reference, "ABDM"],
    )
    return client.get(url, params).content.decode()


def text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def between(html, start, end=None):
    """The text of the page from one element up to another, by their ids."""
    html = html[html.rindex("<", 0, html.index(f'id="{start}"')) :]
    if end:
        html = html[: html.rindex("<", 0, html.index(f'id="{end}"'))]
    return text(html)


def test_the_documents_view_lists_each_file_once_under_its_milestone(
    environment,  # noqa: F811
    client,
):
    submit(environment)
    workflows.reuse_evidence(milestone(environment, "m2"), environment["applicant"])
    client.force_login(environment["applicant"])

    html = page(client, environment, view="documents")

    m1 = between(html, "documents-m1", "documents-m2")
    assert m1.startswith("M1 ABHA Creation and Verification")
    saved = (
        ("WASA certificate", "wasa.pdf"),
        ("Functional testing certificate", "certificate.pdf"),
        ("Functional testing reports", "report.xlsx"),
        ("Undertaking form", "undertaking-form.pdf"),
    )
    for label, name in saved:
        assert f"{label} {name}" in m1
    # M2 reuses M1's evidence: the same files, listed once and named for both.
    assert m1.count("Also M2") == len(saved)
    m2 = between(html, "documents-m2")
    assert "Uses the same files as M1." in m2
    assert "wasa.pdf" not in m2
    chips = between(html, "track-documents", "documents-m1")
    assert "M1 · 4 M2 · 4 M3 · none yet M4 · none yet" in chips
    assert re.findall(r'<a class="ui-switch-option"[^>]*>(\w+)', html) == [
        "Milestones",
        "Documents",
    ]
    assert re.search(r'href="\?view=documents"\s+aria-current="page"', html)
    # A view of the whole track: no milestone is on screen, so none is picked out,
    # and Milestones returns to the track rather than to one of them.
    assert "ui-milestone-tiles" not in html
    track = reverse(
        "experiences:track",
        args=[environment["workspace"].reference, "ABDM"],
    )
    assert re.search(rf'href="{re.escape(track)}"\s*>Milestones', html)


def test_the_milestone_view_counts_the_documents_without_listing_them(
    environment,  # noqa: F811
    client,
):
    submit(environment)
    client.force_login(environment["applicant"])

    html = page(client, environment)

    assert 'id="track-documents"' not in html
    assert 'id="milestone-detail"' in html
    assert "ui-milestone-tiles" in html
    switch = text(html[html.index('aria-label="Track view"') :].split("</nav>")[0])
    assert switch.endswith("Milestones Documents 4")


def test_the_documents_view_says_when_nothing_is_uploaded(environment, client):  # noqa: F811
    client.force_login(environment["applicant"])

    html = page(client, environment, view="documents")

    assert "No documents uploaded yet." in between(html, "track-documents")
