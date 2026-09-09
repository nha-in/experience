"""The ported screens keep the engine's form and review contracts."""

# ruff: noqa: PLR2004

from html.parser import HTMLParser

import pytest
from django.urls import reverse

from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.forms import ProductRegistrationForm
from ohc_experience.abdm.tests import test_workflow as workflow_fixtures
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import stored_secret
from ohc_experience.experiences import workflows
from ohc_experience.integrations.local import fail_next
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.services import provision_inline

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
    assert new["solution_type"].value() == ["clinical_hmis"]
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
    assert b"Opens once M1 is approved" in response.content
    assert b"Same record as HI-CM M1" in response.content


def test_solution_type_accepts_several_values():
    form = ProductRegistrationForm(
        data={
            "name": "Claims platform",
            "description": "Exchanges claims with payers.",
            "category": "claims_platform",
            "solution_type": ["payers", "providers"],
            "payer_category": ["tpa"],
            "applied_milestones": ["HI-CM:m1"],
        },
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["solution_type"] == ["payers", "providers"]


def payer_payload(**overrides):
    return {
        "name": "Claims platform",
        "description": "Exchanges claims with payers.",
        "category": "claims_platform",
        "applied_milestones": ["HI-CM:m1"],
        **overrides,
    }


def test_payer_category_is_required_once_payers_is_chosen():
    form = ProductRegistrationForm(data=payer_payload(solution_type=["payers"]))
    assert not form.is_valid()
    assert "Select at least one payer category." in str(form.errors["payer_category"])


def test_payer_category_is_dropped_when_payers_is_not_chosen():
    form = ProductRegistrationForm(
        data=payer_payload(solution_type=["clinical_hmis"], payer_category=["tpa"]),
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["payer_category"] == []


def test_a_draft_may_leave_payer_category_unanswered():
    form = ProductRegistrationForm(
        data=payer_payload(solution_type=["payers"]),
        draft=True,
    )
    assert form.is_valid(), form.errors


def test_payer_category_starts_hidden_and_opens_with_payers():
    from ohc_experience.experiences.templatetags.experience_ui import (  # noqa: PLC0415
        show_when,
    )

    shut = show_when(ProductRegistrationForm(), "payer_category")
    assert shut == {"field": "solution_type", "value": "payers", "active": False}
    opened = show_when(
        ProductRegistrationForm(initial={"solution_type": ["payers"]}),
        "payer_category",
    )
    assert opened["active"] is True
    assert show_when(ProductRegistrationForm(), "name") is None


@pytest.mark.django_db
def test_editing_a_product_persists_several_solution_types(environment, client):
    client.force_login(environment["applicant"])
    workspace = environment["workspace"]
    item = workspace.product.review_items.get(kind="product_registration")
    payload = dict(item.selected_submission.data)
    payload["solution_type"] = ["clinical_hmis", "pharmacy"]
    payload["revision"] = str(item.selected_submission_id or "")
    payload["intent"] = "submit"
    response = client.post(
        reverse("experiences:product-edit", args=[workspace.reference]),
        payload,
        follow=True,
    )
    assert response.status_code == 200
    workspace.refresh_from_db()
    assert workspace.solution_type == ["clinical_hmis", "pharmacy"]
    assert workspace.get_solution_type_display() == "Clinical HMIS, Pharmacy"


def test_nhcx_track_is_gated_on_m1_approval():
    shut = ProductRegistrationForm()
    row = next(
        row for row in shut.milestone_tracks if row["definition"].code == "NHCX"
    )
    assert row["locked"]
    assert "Opens once M1 is approved" in row["lock_reason"]
    assert not any(
        other["locked"]
        for other in shut.milestone_tracks
        if other["definition"].code != "NHCX"
    )
    open_row = next(
        row
        for row in ProductRegistrationForm(approved_milestones={"m1"}).milestone_tracks
        if row["definition"].code == "NHCX"
    )
    assert not open_row["locked"]
    assert not open_row["lock_reason"]


def test_nhcx_selection_is_refused_until_m1_is_approved():
    payload = {
        "name": "Claims platform",
        "description": "Exchanges claims with payers.",
        "category": "claims_platform",
        "solution_type": ["eua"],
        "applied_milestones": ["HI-CM:m1", "NHCX:nhcx1"],
    }
    forged = ProductRegistrationForm(data=payload)
    assert not forged.is_valid()
    assert "NHCX cannot be selected yet" in str(forged.errors["applied_milestones"])
    allowed = ProductRegistrationForm(data=payload, approved_milestones={"m1"})
    assert allowed.is_valid(), allowed.errors


@pytest.mark.django_db
def test_nhcx_opens_in_edit_product_once_m1_is_approved(environment, client):
    client.force_login(environment["applicant"])
    url = reverse(
        "experiences:product-edit",
        args=[environment["workspace"].reference],
    )
    assert b"Opens once M1 is approved" in client.get(url).content
    approve(environment)
    assert b"Opens once M1 is approved" not in client.get(url).content


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
    plain = stored_secret(environment["workspace"].product)
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


@pytest.mark.django_db
def test_a_pending_panel_shows_what_each_system_is_doing(environment, client):
    """ "No credentials yet" and "the bridge never happened" must not look alike."""
    fail_next(ExternalSystem.HIECM, "create_bridge", retryable=False)
    workspace, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data=product_data("Second product"),
    )
    assert workspace, form.errors
    provision_inline(workspace.product)
    client.force_login(environment["applicant"])

    html = client.get(
        reverse("experiences:credentials", args=[workspace.reference]),
    ).content.decode()

    assert "Provisioning in progress" in html
    assert "Identity" in html
    assert "Bridge" in html
