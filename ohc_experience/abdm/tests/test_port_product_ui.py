"""The ported screens keep the engine's form and review contracts."""

# ruff: noqa: PLR2004

from html.parser import HTMLParser

import pytest
from django.urls import reverse

from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.forms import ProductRegistrationForm
from ohc_experience.abdm.tests import test_workflow as workflow_fixtures
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.experiences import credentials
from ohc_experience.experiences.models import ProductCredential

environment = workflow_fixtures.environment


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            self.fields.append(dict(attrs))


def test_registration_defaults_only_apply_to_new_unbound_forms():
    new = ProductRegistrationForm()
    assert new["category"].value() == "hmis"
    assert new["solution_type"].value() == "clinical_hmis"
    assert new["applied_milestones"].value() == ["HI-CM:m1"]
    assert not ProductRegistrationForm(initial={})["applied_milestones"].value()
    saved = ProductRegistrationForm(
        initial={"applied_milestones": ["UHI:uhi1"], "category": "other"},
    )
    assert saved["applied_milestones"].value() == ["UHI:uhi1"]
    assert saved["category"].value() == "other"
    posted = ProductRegistrationForm(data={"name": "Incomplete"})
    assert not posted["applied_milestones"].value()
    assert not any(
        choice["selected"]
        for track in posted.milestone_tracks
        for choice in track["milestones"]
    )


@pytest.mark.django_db
def test_register_another_product_keeps_new_defaults(environment, client):
    client.force_login(environment["applicant"])
    client.get(environment["workspace"].get_absolute_url())
    response = client.get(reverse("experiences:product-create"))
    assert response.status_code == 200
    inputs = Inputs(response.content.decode()).fields
    selected = [
        field["value"]
        for field in inputs
        if field.get("name") == "applied_milestones" and "checked" in field
    ]
    assert selected == ["HI-CM:m1"]
    assert b"No milestones published yet" in response.content
    assert b"Same record as HI-CM M1" in response.content


@pytest.mark.django_db
def test_approved_picker_carries_locked_selections(environment, client):
    approve(environment)
    client.force_login(environment["applicant"])
    response = client.get(
        reverse("experiences:product-edit", args=[environment["workspace"].reference]),
    )
    assert response.status_code == 200
    inputs = Inputs(response.content.decode()).fields
    carried = [
        field["value"]
        for field in inputs
        if field.get("name") == "applied_milestones" and field.get("type") == "hidden"
    ]
    assert "HI-CM:m1" in carried
    assert "PHR:m1" in carried


@pytest.mark.django_db
def test_track_draft_uploads_and_withdrawn_snapshot_remain_editable(
    environment,
    client,
):
    client.force_login(environment["applicant"])
    url = reverse(
        "experiences:track",
        args=[environment["workspace"].reference, "HI-CM"],
    )
    uploads = {key: value[0] for key, value in files().lists()}
    response = client.post(
        url,
        {**evidence_data(), **uploads, "intent": "draft", "revision": ""},
        follow=True,
    )
    assert response.status_code == 200
    assert b"data-review-form" in response.content
    assert b"certificate.pdf" in response.content
    assert b"data-existing-file-remove" in response.content
    item = milestone(environment)
    response = client.post(
        url,
        {
            **evidence_data(),
            "intent": "submit",
            "revision": str(item.selected_submission_id),
        },
        follow=True,
    )
    assert response.status_code == 200
    assert b"Withdraw request" in response.content
    assert b"data-review-form" not in response.content
    assert b"Submitted form" in response.content
    response = client.post(
        reverse("experiences:withdraw", args=[item.pk]),
        follow=True,
    )
    assert response.status_code == 200
    assert b"data-review-form" in response.content
    assert b"certificate.pdf" in response.content
    assert b"data-existing-file-remove" in response.content


@pytest.mark.django_db
@pytest.mark.parametrize("htmx", [False, True])
def test_credential_reveal_preserves_full_page_fallback(environment, client, htmx):
    credential = ProductCredential.objects.get(product=environment["workspace"].product)
    plain = credentials.cipher().decrypt(credential.encrypted_secret.encode()).decode()
    client.force_login(environment["applicant"])
    url = reverse("experiences:credentials", args=[environment["workspace"].reference])
    assert plain not in client.get(url).content.decode()
    response = client.post(
        url,
        {"intent": "reveal"},
        **({"HTTP_HX_REQUEST": "true"} if htmx else {}),
    )
    assert response.status_code == 200
    assert plain in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert b"data-secret-container" in response.content
    assert (b'id="callback-card"' in response.content) is not htmx
