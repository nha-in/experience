"""The ported screens keep the engine's form and review contracts."""

# ruff: noqa: PLR2004

from html.parser import HTMLParser
from importlib import import_module

import pytest
from django.urls import reverse

from ohc_experience.abdm.catalog import MILESTONES
from ohc_experience.abdm.catalog import REQUIRED_MILESTONES
from ohc_experience.abdm.catalog import TRACK_MAP
from ohc_experience.abdm.definitions import ABDM
from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.demo import uhi_data
from ohc_experience.abdm.forms import ProductRegistrationForm
from ohc_experience.abdm.forms import UhiParticipationForm
from ohc_experience.abdm.tests import test_workflow as workflow_fixtures
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import stored_secret
from ohc_experience.abdm.tests.test_workflow import submit
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
    assert new["applied_milestones"].value() == ["HIE-CM:m1", "HIE-CM:m2", "HIE-CM:m3"]
    assert not any(row["warning"] for row in milestone_rows(new).values())
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
    assert selected == ["HIE-CM:m1", "HIE-CM:m2", "HIE-CM:m3"]
    assert b"M1 required for enablement" in response.content
    assert b"Shared with" not in response.content


def milestone_rows(form):
    return {
        row["definition"].code: row
        for track in form.milestone_tracks
        for row in track["milestones"]
    }


def test_solution_types_require_the_intent_matrix_milestones():
    choices = dict(ProductRegistrationForm.base_fields["solution_type"].choices)
    matrix = {
        choices[solution]: [MILESTONES[key].code for key in keys]
        for solution, keys in REQUIRED_MILESTONES.items()
    }

    assert matrix == {
        "HMIS": ["M1", "M2", "M3"],
        "Clinical HMIS": ["M1", "M2", "M3"],
        "Healthtech": ["M1", "M2", "M3"],
        "LMIS": ["M1", "M2", "M3"],
        "Insurance": ["M1", "M3"],
        "Telemedicine": ["M1", "M2", "M3"],
        "Pharmacy": ["M1", "M2", "M3"],
    }


def test_an_unchecked_required_milestone_warns_but_still_saves():
    form = ProductRegistrationForm(
        data={
            **product_data(),
            "solution_type": ["hmis", "insurance", "pharmacy"],
            "applied_milestones": ["HIE-CM:m1", "HIE-CM:m2"],
        },
    )

    assert form.is_valid(), form.errors
    rows = milestone_rows(form)
    assert rows["M3"]["warning"] == (
        "Required for the HMIS, Insurance and Pharmacy solution types."
    )
    assert not any(rows[code]["warning"] for code in ("M1", "M2", "M4", "UHI1"))


def test_m4_can_be_chosen_without_m1():
    form = ProductRegistrationForm(
        data={**product_data(), "applied_milestones": ["HIE-CM:m4"]},
    )

    assert form.is_valid(), form.errors
    assert ABDM.milestone_keys(form.cleaned_data["applied_milestones"]) == {"m4"}


def test_a_warning_names_only_the_chosen_types_that_require_it():
    form = ProductRegistrationForm(
        data={
            **product_data(),
            "solution_type": ["insurance", "payers"],
            "payer_category": ["tpa"],
            "applied_milestones": ["HIE-CM:m1"],
        },
    )

    rows = milestone_rows(form)
    assert rows["M3"]["warning"] == "Required for the Insurance solution type."
    assert not rows["M2"]["warning"]
    assert "insurance" in rows["M3"]["required_for"].split()
    assert "insurance" not in rows["M2"]["required_for"].split()
    assert not rows["M4"]["required_for"]


@pytest.mark.django_db
def test_the_picker_shows_why_a_saved_product_lacks_a_required_milestone(
    environment,
    client,
):
    workspace, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data={
            **product_data("Claims desk"),
            "solution_type": ["insurance"],
            "applied_milestones": ["HIE-CM:m1"],
        },
    )
    assert workspace, form.errors
    client.force_login(environment["applicant"])

    html = client.get(
        reverse("experiences:product-edit", args=[workspace.reference]),
    ).content.decode()

    inputs = {field.get("id"): field for field in Inputs(html).fields}
    assert "data-solution-type" in inputs["id_solution_type_0"]
    m3 = inputs["milestone-hie-cmm3"]
    assert "insurance" in m3["data-required-for"].split()
    assert m3["aria-describedby"] == "milestone-hie-cmm3-required"
    assert html.count("Required for the Insurance solution type.") == 1


def test_each_track_offers_its_own_milestones_and_names_what_it_needs():
    """M1 belongs to HIE-CM; the other tracks depend on it rather than repeat it."""
    tracks = {
        track["definition"].code: (
            [row["definition"].key for row in track["milestones"]],
            track["requires"],
        )
        for track in ProductRegistrationForm().milestone_tracks
    }

    assert tracks == {
        "HIE-CM": (["m1", "m2", "m3", "m4"], ""),
        "UHI": (["uhi1"], "M1"),
        "NHCX": (["nhcx1"], "M1"),
        "PHR": (["phr1"], "M1"),
        "HealthLocker": (["locker1"], ""),
    }


def test_a_dependant_track_lists_its_prerequisite_once_chosen():
    """The product page shows M1 under UHI without UHI storing it."""
    selections = ["HIE-CM:m1", "UHI:uhi1"]

    assert ABDM.applied_keys(TRACK_MAP["UHI"], selections) == ["m1", "uhi1"]
    assert ABDM.applied_keys(TRACK_MAP["HIE-CM"], selections) == ["m1"]
    assert ABDM.applied_keys(TRACK_MAP["PHR"], selections) == []


def test_stored_inherited_selections_move_to_the_owning_track():
    migration = import_module(
        "ohc_experience.experiences.migrations."
        "0013_drop_inherited_milestone_selections",
    )

    assert migration.drop_inherited(["HIE-CM:m1", "UHI:m1", "UHI:uhi1"]) == [
        "HIE-CM:m1",
        "UHI:uhi1",
    ]
    assert migration.drop_inherited(["PHR:m1", "PHR:phr1"]) == [
        "HIE-CM:m1",
        "PHR:phr1",
    ]
    assert migration.drop_inherited(["HealthLocker:locker1"]) == [
        "HealthLocker:locker1",
    ]


def test_solution_type_accepts_several_values():
    form = ProductRegistrationForm(
        data={
            "name": "Claims platform",
            "description": "Exchanges claims with payers.",
            "category": "claims_platform",
            "solution_type": ["payers", "providers"],
            "payer_category": ["tpa"],
            "applied_milestones": ["HIE-CM:m1"],
        },
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["solution_type"] == ["payers", "providers"]


def payer_payload(**overrides):
    return {
        "name": "Claims platform",
        "description": "Exchanges claims with payers.",
        "category": "claims_platform",
        "applied_milestones": ["HIE-CM:m1"],
        **overrides,
    }


def test_product_category_keeps_the_plain_dropdown():
    html = str(ProductRegistrationForm()["category"])
    assert html.startswith('<select name="category" data-native-select=""')


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


def uhi_payload(**overrides):
    return {
        "name": "Discovery app",
        "description": "Finds and books consultations.",
        "category": "other",
        "solution_type": ["eua"],
        "applied_milestones": ["HIE-CM:m1", "UHI:uhi1"],
        **overrides,
    }


def test_registration_no_longer_asks_about_uhi():
    """Participation is its own request now, not a corner of registration."""
    form = ProductRegistrationForm(data=uhi_payload())

    assert form.is_valid(), form.errors
    assert not [name for name in form.fields if name.startswith("uhi_")]


def test_uhi_participation_requires_a_role_and_a_service():
    form = UhiParticipationForm(data={"uhi_tell_us_about": "Teleconsultation."})

    assert not form.is_valid()
    assert "uhi_role" in form.errors
    assert "uhi_services" in form.errors


def test_uhi_participation_accepts_the_answers_legacy_collected():
    form = UhiParticipationForm(data=uhi_data())

    assert form.is_valid(), form.errors


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
    assert "HIE-CM:m1" in carried
    locked = next(field for field in inputs if field.get("id") == "milestone-hie-cmm1")
    assert "disabled" in locked
    assert "data-required-for" not in locked


@pytest.mark.django_db
def test_picker_locks_a_milestone_under_review_until_it_is_withdrawn(
    environment,
    client,
):
    approve(environment)
    item = submit(environment, "m2")
    client.force_login(environment["applicant"])
    url = reverse("experiences:product-edit", args=[environment["workspace"].reference])

    html = client.get(url).content.decode()
    inputs = Inputs(html).fields
    carried = [
        field["value"]
        for field in inputs
        if field.get("name") == "applied_milestones" and field.get("type") == "hidden"
    ]
    assert carried == ["HIE-CM:m1", "HIE-CM:m2"]
    locked = next(field for field in inputs if field.get("id") == "milestone-hie-cmm2")
    assert "disabled" in locked
    assert locked["aria-describedby"] == "milestone-hie-cmm2-why"
    assert 'id="milestone-hie-cmm2-why">Under review · withdraw to remove<' in html
    assert "1 approved · cannot be removed" in html
    assert "1 under review · withdraw to remove" in html

    workflows.withdraw(item, environment["applicant"])
    html = client.get(url).content.decode()
    inputs = Inputs(html).fields
    unlocked = next(
        field for field in inputs if field.get("id") == "milestone-hie-cmm2"
    )
    assert "disabled" not in unlocked
    assert unlocked["name"] == "applied_milestones"
    assert "under review · withdraw to remove" not in html.lower()


@pytest.mark.django_db
def test_track_draft_uploads_and_withdrawn_snapshot_remain_editable(
    environment,
    client,
):
    client.force_login(environment["applicant"])
    url = reverse(
        "experiences:track",
        args=[environment["workspace"].reference, "HIE-CM"],
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
