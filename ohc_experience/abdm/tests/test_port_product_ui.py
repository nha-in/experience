"""The ported screens keep the engine's form and review contracts."""

# ruff: noqa: PLR2004

import re
from html import unescape
from html.parser import HTMLParser
from importlib import import_module

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
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
from ohc_experience.abdm.tests.test_workflow import OTHER_TYPE
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import clear_callback_url
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import registered_as_other
from ohc_experience.abdm.tests.test_workflow import review_section
from ohc_experience.abdm.tests.test_workflow import stored_secret
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import legacy
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.integrations.local import fail_next
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.services import provision_inline
from ohc_experience.organisations.models import GOVERNMENT
from ohc_experience.organisations.models import Organisation

environment = workflow_fixtures.environment


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            self.fields.append(dict(attrs))


def test_nothing_is_ticked_until_the_integrator_chooses():
    """Registering and editing both start from what the product has, not a guess."""
    new = ProductRegistrationForm()
    assert not new["solution_type"].value()
    assert not new["applied_milestones"].value()
    assert not any(row["selected"] for row in milestone_rows(new).values())
    assert not ProductRegistrationForm(initial={})["applied_milestones"].value()
    saved = ProductRegistrationForm(
        initial={"applied_milestones": ["UHI:uhi1"], "solution_type": ["other"]},
    )
    assert saved["applied_milestones"].value() == ["UHI:uhi1"]
    assert saved["solution_type"].value() == ["other"]
    posted = ProductRegistrationForm(data={"name": "Incomplete"})
    assert not posted["applied_milestones"].value()
    assert not any(
        choice["selected"]
        for track in posted.milestone_tracks
        for choice in track["milestones"]
    )


@pytest.mark.django_db
def test_the_registration_page_preselects_nothing(environment, client):
    client.force_login(environment["applicant"])
    client.get(environment["product"].get_absolute_url())
    response = client.get(reverse("experiences:product-create"))
    assert response.status_code == 200
    inputs = Inputs(response.content.decode()).fields
    checked = [
        field["value"]
        for field in inputs
        if field.get("name") in {"applied_milestones", "solution_type"}
        and "checked" in field
    ]
    assert checked == []
    assert b"M1 or P1 required for enablement" in response.content
    # Neither exclusive track is chosen yet, so both sentences sit on the page
    # hidden, ready for the script the moment one side is ticked.
    assert response.content.count(b"Not in implementation scope of") == 2
    assert b"Not in implementation scope of ABDM." in response.content
    assert b"Shared with" not in response.content
    assert b"About Clinic HMIS" in response.content
    assert b'popovertarget="info-solution-clinical_hmis"' in response.content
    assert b'id="info-solution-clinical_hmis"' in response.content
    assert b"/concepts/hip-hiu" in response.content
    assert b"/concepts/participants/pharmacy" in response.content
    assert b"/concepts/participants/phr" in response.content
    assert b"/concepts/participants/insurer" in response.content
    assert b"/concepts/participants/lab" in response.content


@pytest.mark.django_db
def test_each_track_heading_has_an_info_button_with_its_documentation(
    environment,
    client,
):
    client.force_login(environment["applicant"])

    response = client.get(reverse("experiences:product-create"))

    picker = response.content.decode().split('id="id_applied_milestones"', 1)[1]
    documented = {
        unescape(label): url
        for label, url in re.findall(
            r'aria-label="About ([^"]+)".+?href="([^"]+)"[^>]*>Open documentation',
            picker,
            re.S,
        )
    }
    assert {track.name: documented.get(track.name) for track in TRACK_MAP.values()} == {
        track.name: track.docs_url for track in TRACK_MAP.values()
    }
    # One page each, not the program's documentation five times over.
    assert len({track.docs_url for track in TRACK_MAP.values()}) == len(TRACK_MAP)


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
        "Clinic HMIS": ["M1", "M2", "M3"],
        "LMIS": ["M1", "M2", "M3"],
        "Pharmacy": ["M1", "M2", "M3"],
        "PHR": ["P1", "P2", "P3"],
        "Health Locker": ["P1", "P2", "P3", "P4"],
        "HealthTech": ["M1", "M2", "M3"],
        "Insurance": ["M1", "M3"],
        "Telemedicine": ["M1", "M2", "M3"],
        "Government Programme": ["M1", "M2", "M3"],
    }


def test_a_solution_type_fixes_its_abdm_or_phr_milestones():
    """Whatever is posted, the type's milestones are what is saved."""

    def saved(solution, *selections):
        form = ProductRegistrationForm(
            data={
                **product_data(),
                "solution_type": [solution],
                "applied_milestones": list(selections),
            },
        )
        assert form.is_valid(), form.errors
        return form.cleaned_data["applied_milestones"]

    assert saved("hmis", "ABDM:m1", "UHI:uhi1") == [
        "UHI:uhi1",
        "ABDM:m1",
        "ABDM:m2",
        "ABDM:m3",
    ]
    assert saved("insurance", "ABDM:m1", "ABDM:m2", "NHCX:nhcx_payer") == [
        "NHCX:nhcx_payer",
        "ABDM:m1",
        "ABDM:m3",
    ]
    assert saved("health_locker", "ABDM:m1") == [
        "PHR:p1",
        "PHR:p2",
        "PHR:p3",
        "PHR:p4",
    ]


def test_m4_stays_the_integrators_choice_under_a_fixed_type():
    """NHA: M4 is optional and checkable; the type fixes only M1 to M3."""

    def saved(solution, *selections):
        form = ProductRegistrationForm(
            data={
                **product_data(),
                "solution_type": [solution],
                "applied_milestones": list(selections),
            },
        )
        assert form.is_valid(), form.errors
        return form.cleaned_data["applied_milestones"]

    assert saved("hmis", "ABDM:m4") == ["ABDM:m4", "ABDM:m1", "ABDM:m2", "ABDM:m3"]
    assert saved("hmis") == ["ABDM:m1", "ABDM:m2", "ABDM:m3"]
    assert saved("insurance", "ABDM:m4") == ["ABDM:m4", "ABDM:m1", "ABDM:m3"]
    # A PHR type leaves no ABDM milestone behind, M4 included.
    assert saved("phr", "ABDM:m4") == ["PHR:p1", "PHR:p2", "PHR:p3"]
    assert not any("m4" in keys for keys in REQUIRED_MILESTONES.values()), (
        "M4 is never fixed by a solution type"
    )
    rows = milestone_rows(
        ProductRegistrationForm(
            data={
                **product_data(),
                "solution_type": ["hmis"],
                "applied_milestones": [],
            },
        ),
    )
    assert rows["M4"]["optional"]
    assert not rows["M3"]["optional"]


def test_a_fixed_type_needs_nothing_posted():
    """Locked boxes are disabled, so the browser posts none of them."""
    form = ProductRegistrationForm(
        data={**product_data(), "solution_type": ["phr"], "applied_milestones": []},
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["applied_milestones"] == ["PHR:p1", "PHR:p2", "PHR:p3"]
    other = ProductRegistrationForm(
        data={**product_data(), **OTHER_TYPE, "applied_milestones": []},
    )
    assert other.errors["applied_milestones"] == ["This field is required."]


@pytest.mark.django_db
def test_registering_with_only_a_fixed_type_ticked_saves_its_milestones(
    environment,
    client,
):
    """Picking HMIS and nothing else posts no milestones at all."""
    client.force_login(environment["applicant"])
    data = product_data("Ward system")
    data.pop("applied_milestones")

    response = client.post(
        reverse("experiences:product-create"),
        {**data, "solution_type": "hmis"},
    )

    assert response.status_code == 302
    product = Product.objects.get(name="Ward system")
    assert product.applied_milestones == ["ABDM:m1", "ABDM:m2", "ABDM:m3"]


@pytest.mark.django_db
def test_unticking_the_last_open_milestone_on_edit_keeps_the_fixed_ones(
    environment,
    client,
):
    """A PHR product drops UHI: P1 to P3 are locked, so nothing is posted."""
    product, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data={
            **product_data("Health app"),
            "solution_type": ["phr"],
            "applied_milestones": ["PHR:p1", "PHR:p2", "PHR:p3", "UHI:uhi1"],
        },
    )
    assert product, form.errors
    item = product.review_items.get(kind="product_registration")
    client.force_login(environment["applicant"])
    data = product_data("Health app")
    data.pop("applied_milestones")

    response = client.post(
        reverse("experiences:product-edit", args=[product.reference]),
        {
            **data,
            "revision": str(item.selected_submission_id or ""),
            "intent": "submit",
        },
    )

    assert response.status_code == 302
    product.refresh_from_db()
    assert product.applied_milestones == ["PHR:p1", "PHR:p2", "PHR:p3"]


def test_the_picker_locks_the_milestones_a_solution_type_fixes():
    form = ProductRegistrationForm(
        data={**product_data(), "solution_type": ["insurance"]},
    )
    tracks = {track["definition"].code: track for track in form.milestone_tracks}
    rows = milestone_rows(form)

    assert tracks["ABDM"]["locked"]
    assert tracks["PHR"]["locked"]
    assert not tracks["UHI"]["locked"]
    assert not tracks["NHCX"]["locked"]
    assert tracks["ABDM"]["fixed_note"] == "Set by the Insurance solution type."
    assert tracks["PHR"]["fixed_note"] == ""
    # M2 goes, but the posted M4 stays: it is never the type's to remove.
    assert [code for code, row in rows.items() if row["selected"]] == [
        "M1",
        "M3",
        "M4",
        "UHI",
    ]


def test_m4_needs_m1_unless_the_entity_is_a_government_body():
    """A government body registers facilities under its own authority."""

    def form(organisation=None):
        return ProductRegistrationForm(
            data={**product_data(), **OTHER_TYPE, "applied_milestones": ["ABDM:m4"]},
            organisation=organisation,
        )

    government = Organisation(entity_type=GOVERNMENT)
    refused = form(Organisation(entity_type="private_company"))
    allowed = form(government)

    assert not refused.is_valid()
    assert refused.errors["applied_milestones"] == [
        "Select M1 before Register Healthcare Professionals and Facilities.",
    ]
    assert allowed.is_valid(), allowed.errors
    selections = allowed.cleaned_data["applied_milestones"]
    assert ABDM.milestone_keys(selections, government) == {"m4"}


def test_p4_needs_p1_p2_and_p3_not_just_one_of_them():
    """A health locker is never applied for alone: it builds on the PHR sequence."""

    def form(*keys):
        return ProductRegistrationForm(
            data={
                **product_data(),
                **OTHER_TYPE,
                "applied_milestones": [f"PHR:{key}" for key in keys],
            },
        )

    for keys in (("p4",), ("p1", "p4"), ("p1", "p2", "p4")):
        refused = form(*keys)
        assert not refused.is_valid(), keys
        assert refused.errors["applied_milestones"] == [
            "Select P1, P2 and P3 before Locker.",
        ]
    allowed = form("p1", "p2", "p3", "p4")
    assert allowed.is_valid(), allowed.errors
    selections = allowed.cleaned_data["applied_milestones"]
    assert ABDM.milestone_keys(selections) == {"p1", "p2", "p3", "p4"}
    with pytest.raises(ValidationError, match="Select P1, P2 and P3 before Locker"):
        ABDM.milestone_keys(["PHR:p1", "PHR:p4"])
    assert REQUIRED_MILESTONES["health_locker"] == ("p1", "p2", "p3", "p4")


def test_other_leaves_abdm_and_phr_to_the_integrator():
    form = ProductRegistrationForm(
        data={
            **product_data(),
            "solution_type": ["insurance", "other"],
            "solution_type_other": "Claims desk",
            "applied_milestones": ["ABDM:m1"],
        },
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["applied_milestones"] == ["ABDM:m1"]
    assert not any(track["locked"] for track in form.milestone_tracks)
    rows = milestone_rows(form)
    assert "insurance" in rows["M3"]["required_for"].split()
    assert "insurance" not in rows["M2"]["required_for"].split()
    assert not rows["UHI"]["required_for"]


@pytest.mark.django_db
def test_editing_keeps_the_solution_type_and_locks_its_milestones(
    environment,
    client,
):
    product, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data={
            **product_data("Claims desk"),
            "solution_type": ["insurance"],
            "applied_milestones": ["ABDM:m1"],
        },
    )
    assert product, form.errors
    assert product.applied_milestones == ["ABDM:m1", "ABDM:m3"]
    client.force_login(environment["applicant"])
    url = reverse("experiences:product-edit", args=[product.reference])

    html = client.get(url).content.decode()

    inputs = {field.get("id"): field for field in Inputs(html).fields}
    radios = [
        field for field in inputs.values() if field.get("name") == "solution_type"
    ]
    assert radios
    assert all("disabled" in field for field in radios)
    for key, ticked in (("m1", True), ("m2", False), ("m3", True)):
        box = inputs[f"milestone-abdm{key}"]
        assert "disabled" in box
        assert "data-milestone-locked" in box
        assert ("checked" in box) is ticked
    # M4 is the integrator's choice under any type.
    m4 = inputs["milestone-abdmm4"]
    assert "disabled" not in m4
    assert "data-milestone-locked" not in m4
    assert "data-milestone-optional" in m4
    assert "checked" not in m4
    assert "disabled" not in inputs["milestone-uhiuhi1"]
    assert "Set by the Insurance solution type." in html

    item = product.review_items.get(kind="product_registration")
    response = client.post(
        url,
        {
            **product_data("Claims desk"),
            "solution_type": ["hmis"],
            "applied_milestones": ["ABDM:m1", "ABDM:m2", "UHI:uhi1"],
            "revision": str(item.selected_submission_id or ""),
            "intent": "submit",
        },
    )
    assert response.status_code == 302
    product.refresh_from_db()
    assert product.solution_type == ["insurance"]
    assert sorted(product.applied_milestones) == ["ABDM:m1", "ABDM:m3", "UHI:uhi1"]


def test_each_track_offers_its_own_milestones_and_names_what_it_needs():
    """Each track carries its own identity milestone; UHI takes either, and NHCX
    names what each role needs on its own row instead."""
    tracks = {
        track["definition"].code: (
            [row["definition"].key for row in track["milestones"]],
            track["requires"],
            track["related"],
        )
        for track in ProductRegistrationForm().milestone_tracks
    }

    assert tracks == {
        "ABDM": (["m1", "m2", "m3", "m4"], "", ""),
        "UHI": (["uhi1"], "M1 or P1", "M2"),
        "NHCX": (["nhcx_payer", "nhcx_provider", "nhcx_patient_app"], "", ""),
        "PHR": (["p1", "p2", "p3", "p4"], "", ""),
    }


def test_both_exclusive_tracks_carry_the_sentence_that_greys_them_out():
    """The script only shows and hides it, so it is on the page either way."""

    def rows(*selections):
        form = ProductRegistrationForm(
            data={
                **product_data(),
                **OTHER_TYPE,
                "applied_milestones": list(selections),
            },
        )
        return {track["definition"].code: track for track in form.milestone_tracks}

    on_abdm = rows("ABDM:m1")
    on_phr = rows("PHR:p1")

    for chosen, ruled_out in (("ABDM", "PHR"), ("PHR", "ABDM")):
        picked = on_abdm if chosen == "ABDM" else on_phr
        assert picked[ruled_out]["blocked"] is True
        assert picked[chosen]["blocked"] is False
        # Both sentences are rendered whichever track was chosen.
        assert picked["ABDM"]["exclusion"] == "Not in implementation scope of PHR."
        assert picked["PHR"]["exclusion"] == "Not in implementation scope of ABDM."
    assert on_abdm["UHI"]["exclusion"] == ""
    assert on_abdm["UHI"]["blocked"] is False


def test_abdm_and_phr_are_refused_together():
    """The script only greys the other track out; saving still refuses the pair."""
    form = ProductRegistrationForm(
        data={
            **product_data(),
            **OTHER_TYPE,
            "applied_milestones": ["PHR:p1", "ABDM:m1"],
        },
    )

    assert not form.is_valid()
    assert form.errors["applied_milestones"] == [
        "ABDM and PHR cannot be applied for together. Choose one of them.",
    ]


def test_a_dependant_track_lists_its_prerequisite_once_chosen():
    """The product page shows M1 and M2 under UHI without UHI storing them."""
    selections = ["ABDM:m1", "ABDM:m2", "UHI:uhi1"]

    assert ABDM.applied_keys(TRACK_MAP["UHI"], selections) == [
        "m1",
        "p1",
        "m2",
        "uhi1",
    ]
    assert ABDM.applied_keys(TRACK_MAP["ABDM"], selections) == ["m1", "m2"]
    assert ABDM.applied_keys(TRACK_MAP["PHR"], selections) == []


def test_stored_inherited_selections_move_to_the_owning_track():
    migration = import_module(
        "ohc_experience.experiences.migrations."
        "0013_drop_inherited_milestone_selections",
    )

    # 0013 predates the track's rename to ABDM, and owns "HIE-CM:m1" for good.
    assert migration.drop_inherited(["HIE-CM:m1", "UHI:m1", "UHI:uhi1"]) == [
        "HIE-CM:m1",
        "UHI:uhi1",
    ]
    assert migration.drop_inherited(["PHR:m1", "PHR:p1"]) == [
        "HIE-CM:m1",
        "PHR:p1",
    ]
    assert migration.drop_inherited(["HealthLocker:p4"]) == [
        "HealthLocker:p4",
    ]


@pytest.mark.django_db
def test_one_solution_type_is_chosen_and_saved_as_a_list(environment, client):
    client.force_login(environment["applicant"])
    url = reverse("experiences:product-create")

    inputs = Inputs(client.get(url).content.decode()).fields
    radios = [field for field in inputs if field.get("name") == "solution_type"]
    assert len(radios) == len(ABDM.solution_types)
    assert all(field["type"] == "radio" and "required" in field for field in radios)

    # A browser posts the one radio chosen.
    client.post(url, {**product_data("Dispensary"), "solution_type": "pharmacy"})
    product = Product.objects.get(name="Dispensary")
    assert product.solution_type == ["pharmacy"]


def test_solution_type_accepts_several_values():
    form = ProductRegistrationForm(
        data={
            "name": "Claims platform",
            "description": "Exchanges claims with payers.",
            "solution_type": ["insurance", "telemedicine"],
            "applied_milestones": ["ABDM:m1"],
        },
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["solution_type"] == ["insurance", "telemedicine"]


def other_payload(**overrides):
    return {
        "name": "Queue desk",
        "description": "Manages patient queues at the front desk.",
        "solution_type": ["other"],
        "applied_milestones": ["ABDM:m1"],
        **overrides,
    }


def test_other_solution_type_needs_a_description():
    form = ProductRegistrationForm(data=other_payload())
    assert not form.is_valid()
    assert form.errors["solution_type_other"] == ["Describe the other solution type."]

    form = ProductRegistrationForm(
        data=other_payload(solution_type_other="Queue management system"),
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["solution_type_other"] == "Queue management system"


def test_other_description_is_dropped_when_other_is_not_chosen():
    form = ProductRegistrationForm(
        data=other_payload(solution_type=["hmis"], solution_type_other="Leftover"),
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["solution_type_other"] == ""


def test_a_draft_may_leave_the_other_description_empty():
    form = ProductRegistrationForm(data=other_payload(), draft=True)
    assert form.is_valid(), form.errors


def test_other_description_starts_hidden_and_opens_with_other():
    from ohc_experience.experiences.templatetags.experience_ui import (  # noqa: PLC0415
        show_when,
    )

    shut = show_when(ProductRegistrationForm(), "solution_type_other")
    assert shut == {"field": "solution_type", "value": "other", "active": False}
    opened = show_when(
        ProductRegistrationForm(initial={"solution_type": ["other"]}),
        "solution_type_other",
    )
    assert opened["active"] is True


def uhi_payload(**overrides):
    return {
        "name": "Discovery app",
        "description": "Finds and books consultations.",
        **OTHER_TYPE,
        "applied_milestones": ["ABDM:m1", "ABDM:m2", "UHI:uhi1"],
        **overrides,
    }


def test_registration_no_longer_asks_about_uhi():
    """Participation is its own request now, not a corner of registration."""
    form = ProductRegistrationForm(data=uhi_payload())

    assert form.is_valid(), form.errors
    assert not [name for name in form.fields if name.startswith("uhi_")]


def test_uhi_can_be_chosen_with_only_m1():
    """UHI's hard prerequisite loosened to M1 alone; M2 is shown, not required."""
    form = ProductRegistrationForm(
        data=uhi_payload(applied_milestones=["ABDM:m1", "UHI:uhi1"]),
    )

    assert form.is_valid(), form.errors


def test_uhi_may_be_chosen_alone_but_nhcx_may_not():
    """UHI alone is applied for and assessed; NHCX always rides on a track."""
    uhi = ProductRegistrationForm(data=uhi_payload(applied_milestones=["UHI:uhi1"]))
    nhcx = ProductRegistrationForm(
        data={
            **product_data(),
            **OTHER_TYPE,
            "applied_milestones": ["NHCX:nhcx_provider"],
        },
    )

    assert uhi.is_valid(), uhi.errors
    assert not nhcx.is_valid()
    assert nhcx.errors["applied_milestones"] == [
        "Select M1 and M2 before Claims exchange as a provider.",
    ]


def nhcx_form(*selections):
    return ProductRegistrationForm(
        data={**product_data(), **OTHER_TYPE, "applied_milestones": list(selections)},
    )


def test_an_nhcx_payer_needs_m1_and_m3():
    for selections in (["ABDM:m1"], ["ABDM:m1", "ABDM:m2"], ["PHR:p1"]):
        refused = nhcx_form(*selections, "NHCX:nhcx_payer")
        assert not refused.is_valid(), selections
        assert refused.errors["applied_milestones"] == [
            "Select M1 and M3 before Claims exchange as a payer.",
        ]
    allowed = nhcx_form("ABDM:m1", "ABDM:m3", "NHCX:nhcx_payer")
    assert allowed.is_valid(), allowed.errors


def test_an_nhcx_provider_needs_m1_and_m2():
    for selections in (["ABDM:m1"], ["ABDM:m1", "ABDM:m3"], ["PHR:p1"]):
        refused = nhcx_form(*selections, "NHCX:nhcx_provider")
        assert not refused.is_valid(), selections
        assert refused.errors["applied_milestones"] == [
            "Select M1 and M2 before Claims exchange as a provider.",
        ]
    allowed = nhcx_form("ABDM:m1", "ABDM:m2", "NHCX:nhcx_provider")
    assert allowed.is_valid(), allowed.errors


def test_a_product_joins_nhcx_in_one_role_only():
    form = nhcx_form(
        "ABDM:m1",
        "ABDM:m2",
        "ABDM:m3",
        "NHCX:nhcx_payer",
        "NHCX:nhcx_provider",
    )

    assert not form.is_valid()
    assert form.errors["applied_milestones"] == [
        "Choose one NHCX role: Payer, Provider or Patient app.",
    ]


def test_a_phr_product_joins_nhcx_as_a_patient_app_on_p1():
    """The docs' end-user application: a PHR app told how a claim moves."""
    allowed = nhcx_form("PHR:p1", "NHCX:nhcx_patient_app")
    refused = nhcx_form("ABDM:m1", "NHCX:nhcx_patient_app")

    assert allowed.is_valid(), allowed.errors
    assert not refused.is_valid()
    assert refused.errors["applied_milestones"] == [
        "Select P1 before Claim updates in a patient app.",
    ]


def test_each_nhcx_role_names_what_it_needs_on_its_own_row():
    rows = milestone_rows(ProductRegistrationForm())

    assert rows["Payer"]["needs"] == "M1 and M3"
    assert rows["Provider"]["needs"] == "M1 and M2"
    assert rows["Patient app"]["needs"] == "P1"
    assert rows["Payer"]["excludes"] == "NHCX:nhcx_provider"
    assert rows["Provider"]["excludes"] == "NHCX:nhcx_payer"
    assert rows["Patient app"]["excludes"] == ""
    # Within a track the order says it, and UHI's note sits on the track.
    assert not any(rows[code]["needs"] for code in ("M2", "P4", "UHI"))


def test_nhcx_shows_the_roles_of_the_track_chosen():
    """ABDM brings Payer and Provider, PHR the patient app; before either, all three."""

    def shown(*selections):
        rows = milestone_rows(nhcx_form(*selections))
        return [
            code
            for code in ("Payer", "Provider", "Patient app")
            if not rows[code]["hidden"]
        ]

    assert shown() == ["Payer", "Provider", "Patient app"]
    assert shown("ABDM:m1") == ["Payer", "Provider"]
    assert shown("PHR:p1") == ["Patient app"]
    assert milestone_rows(nhcx_form())["Patient app"]["role_track"] == "PHR"


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
        reverse("experiences:product-edit", args=[environment["product"].reference]),
    )
    assert response.status_code == 200
    inputs = Inputs(response.content.decode()).fields
    carried = [
        field["value"]
        for field in inputs
        if field.get("name") == "applied_milestones" and field.get("type") == "hidden"
    ]
    assert "ABDM:m1" in carried
    locked = next(field for field in inputs if field.get("id") == "milestone-abdmm1")
    assert "disabled" in locked
    assert "data-required-for" not in locked


@pytest.mark.django_db
def test_picker_locks_a_milestone_under_review_until_it_is_withdrawn(
    environment,
    client,
):
    approve(environment)
    registered_as_other(environment["product"])
    item = submit(environment, "m2")
    client.force_login(environment["applicant"])
    url = reverse("experiences:product-edit", args=[environment["product"].reference])

    html = client.get(url).content.decode()
    inputs = Inputs(html).fields
    carried = [
        field["value"]
        for field in inputs
        if field.get("name") == "applied_milestones" and field.get("type") == "hidden"
    ]
    assert carried == ["ABDM:m1", "ABDM:m2"]
    locked = next(field for field in inputs if field.get("id") == "milestone-abdmm2")
    assert "disabled" in locked
    assert locked["aria-describedby"] == "milestone-abdmm2-why"
    assert 'id="milestone-abdmm2-why">Under review · withdraw to remove<' in html
    assert "1 approved · cannot be removed" in html
    assert "1 under review · withdraw to remove" in html

    workflows.withdraw(item, environment["applicant"])
    html = client.get(url).content.decode()
    inputs = Inputs(html).fields
    unlocked = next(field for field in inputs if field.get("id") == "milestone-abdmm2")
    assert "disabled" not in unlocked
    assert unlocked["name"] == "applied_milestones"
    assert "under review · withdraw to remove" not in html.lower()


@pytest.mark.django_db
def test_nhcx_roles_are_radios_and_one_under_review_locks_the_other(
    environment,
    client,
):
    product = workflow_fixtures.nhcx_product(environment)
    url = reverse("experiences:product-edit", args=[product.reference])
    client.force_login(environment["applicant"])

    def role(html, key):
        inputs = Inputs(html).fields
        return next(
            field for field in inputs if field.get("id") == f"milestone-nhcx{key}"
        )

    html = client.get(url).content.decode()
    payer = role(html, "nhcx_payer")
    provider = role(html, "nhcx_provider")
    patient_app = role(html, "nhcx_patient_app")
    assert payer["type"] == provider["type"] == patient_app["type"] == "radio"
    assert payer["name"] == provider["name"] == "applied_milestones"
    assert "data-nhcx-clear" in html
    # An ABDM product: the patient app is PHR's, so its row starts hidden.
    assert 'data-role-track="PHR" hidden' in html
    assert 'data-role-track="ABDM" hidden' not in html

    workflow_fixtures.submit_claims(environment, "m1")
    workflow_fixtures.submit_claims(environment, "m3")
    workflow_fixtures.submit_claims(environment, "nhcx_payer")
    html = client.get(url).content.decode()
    provider = role(html, "nhcx_provider")
    assert "disabled" in provider
    assert "data-role-locked" in provider
    assert "Not in implementation scope of Payer" in html


@pytest.mark.django_db
def test_track_draft_uploads_and_withdrawn_snapshot_remain_editable(
    environment,
    client,
):
    client.force_login(environment["applicant"])
    url = reverse(
        "experiences:track",
        args=[environment["product"].reference, "ABDM"],
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
    plain = stored_secret(environment["product"])
    client.force_login(environment["applicant"])
    url = reverse("experiences:credentials", args=[environment["product"].reference])
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
def test_saving_a_new_callback_url_replaces_the_old_one(environment, client):
    credential = ProductCredential.objects.get(product=environment["product"])
    credential.callback_url = "https://old.example/callback"
    credential.save(update_fields=["callback_url"])
    client.force_login(environment["applicant"])

    response = client.post(
        reverse("experiences:credentials", args=[environment["product"].reference]),
        {"intent": "callback", "callback_url": "https://new.example/callback"},
    )

    assert response.status_code == 302
    assert [str(message) for message in get_messages(response.wsgi_request)] == [
        (
            "Callback URL saved. Registering it with the gateway — reload in a few "
            "minutes to see whether it went through."
        ),
    ]
    credential.refresh_from_db()
    assert credential.callback_url == "https://new.example/callback"


@pytest.mark.django_db
def test_trying_the_gateway_again_says_what_it_is_doing(environment, client):
    credential = ProductCredential.objects.get(product=environment["product"])
    credential.callback_url = "https://kept.example/callback"
    credential.save(update_fields=["callback_url"])
    client.force_login(environment["applicant"])

    response = client.post(
        reverse("experiences:credentials", args=[environment["product"].reference]),
        {"intent": "register"},
    )

    assert response.status_code == 302
    assert [str(message) for message in get_messages(response.wsgi_request)] == [
        (
            "Registering your callback URL with the gateway again — reload in a few "
            "minutes to see whether it went through."
        ),
    ]


@pytest.mark.django_db
def test_a_plain_http_callback_url_is_refused(environment, client):
    credential = ProductCredential.objects.get(product=environment["product"])
    credential.callback_url = "https://kept.example/callback"
    credential.save()
    client.force_login(environment["applicant"])

    response = client.post(
        reverse("experiences:credentials", args=[environment["product"].reference]),
        {"intent": "callback", "callback_url": "http://plain.example/callback"},
    )

    assert response.status_code == 200
    assert "Use an HTTPS URL." in response.content.decode()
    credential.refresh_from_db()
    assert credential.callback_url == "https://kept.example/callback"


@pytest.mark.django_db
def test_a_pending_panel_shows_what_each_system_is_doing(environment, client):
    """ "No credentials yet" and "the gateway never happened" must not look alike."""
    fail_next(ExternalSystem.WSO2, "create_application", retryable=False)
    product, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data=product_data("Second product"),
    )
    assert product, form.errors
    provision_inline(product)
    client.force_login(environment["applicant"])

    html = client.get(
        reverse("experiences:credentials", args=[product.reference]),
    ).content.decode()

    assert "Provisioning in progress" in html
    assert "Identity" in html
    assert "Gateway" in html


def _track_url(product, milestone):
    return (
        reverse("experiences:track", args=[product.reference, "ABDM"])
        + f"?milestone={milestone}"
    )


@pytest.mark.django_db
def test_an_m1_only_product_is_never_asked_for_a_callback_url(environment, client):
    """The gateway never calls an M1-only integrator back."""
    product, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data=product_data("Identity only")
        | OTHER_TYPE
        | {"applied_milestones": ["ABDM:m1"]},
    )
    assert product, form.errors
    provision_inline(product)
    client.force_login(environment["applicant"])

    html = client.get(
        reverse("experiences:credentials", args=[product.reference]),
    ).content.decode()

    assert product.needs_callback is False
    assert "Callback URL" not in html


@pytest.mark.django_db
def test_a_product_doing_m2_is_asked_for_one(environment, client):
    client.force_login(environment["applicant"])

    html = client.get(
        reverse("experiences:credentials", args=[environment["product"].reference]),
    ).content.decode()

    assert environment["product"].needs_callback is True
    assert "Callback URL" in html


@pytest.mark.django_db
def test_the_m2_page_says_when_no_callback_url_is_saved(environment, client):
    clear_callback_url(environment)
    client.force_login(environment["applicant"])

    html = client.get(_track_url(environment["product"], "m2")).content.decode()

    assert "No callback URL saved" in html
    # Underlined, as a link in an alert is, so it does not read as plain text.
    credentials = reverse(
        "experiences:credentials",
        args=[environment["product"].reference],
    )
    assert re.search(
        r'<a class="font-semibold underline underline-offset-4"\s+'
        rf'href="{credentials}">Add a callback URL</a>',
        html,
    )


@pytest.mark.django_db
def test_the_m1_page_does_not(environment, client):
    clear_callback_url(environment)
    client.force_login(environment["applicant"])

    html = client.get(_track_url(environment["product"], "m1")).content.decode()

    assert "No callback URL saved" not in html


@pytest.mark.django_db
@pytest.mark.parametrize(("key", "code"), [("m2", "M2"), ("uhi1", "UHI")])
def test_a_milestone_that_calls_back_needs_a_callback_url_to_submit(
    environment,
    key,
    code,
):
    submit(environment, "m1")
    clear_callback_url(environment)

    with pytest.raises(ValidationError, match=f"Add a callback URL to submit {code}."):
        submit(environment, key)


@pytest.mark.django_db
def test_m1_is_submitted_without_one(environment):
    clear_callback_url(environment)

    submit(environment, "m1")


@pytest.mark.django_db
def test_the_m2_page_disables_submit_until_a_callback_url_is_saved(
    environment,
    client,
):
    submit(environment, "m1")
    clear_callback_url(environment)
    client.force_login(environment["applicant"])
    url = _track_url(environment["product"], "m2")

    html = client.get(url).content.decode()

    assert "disabled" in submit_button(html)
    assert 'data-submit-blocked="true"' in html
    assert "No callback URL saved" in html

    ProductCredential.objects.filter(product=environment["product"]).update(
        callback_url="https://integrator.example/callback",
    )
    html = client.get(url).content.decode()

    assert "disabled" not in submit_button(html)
    assert "data-submit-blocked" not in html
    assert "Add a callback URL" not in html


def submit_button(html):
    return re.search(r"<button[^>]*data-request-submit[^>]*>", html).group()


@pytest.mark.django_db
def test_the_product_page_flags_a_missing_callback_url(environment, client):
    submit(environment, "m1")
    submit(environment, "m2")
    clear_callback_url(environment)
    url = reverse(
        "experiences:product-detail",
        args=[environment["product"].reference],
    )
    client.force_login(environment["reviewer"])

    html = client.get(url).content.decode()

    assert "No callback URL saved" in review_section(html, milestone(environment, "m2"))
    assert "No callback URL saved" not in review_section(
        html,
        milestone(environment, "m1"),
    )


@pytest.mark.django_db
def test_the_product_page_does_not_flag_it_once_a_callback_url_is_saved(
    environment,
    client,
):
    submit(environment, "m1")
    submit(environment, "m2")
    url = reverse(
        "experiences:product-detail",
        args=[environment["product"].reference],
    )
    client.force_login(environment["reviewer"])

    html = client.get(url).content.decode()

    assert "No callback URL saved" not in html


@pytest.mark.django_db
def test_saving_a_callback_url_reports_its_registration_on_reload(
    environment,
    client,
    django_capture_on_commit_callbacks,
):
    client.force_login(environment["applicant"])
    url = reverse("experiences:credentials", args=[environment["product"].reference])

    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            url,
            {"intent": "callback", "callback_url": "https://acme.example/callback"},
        )

    html = client.get(url).content.decode()
    assert "Accepted" in html
    # The gateway cannot be read back, so the card must not claim more.
    assert "Reachable" not in html


def posted(solution, *selections):
    """The form as the browser posts it after a switch: locked boxes are
    disabled, so only what the integrator could still tick arrives."""
    return ProductRegistrationForm(
        data={
            **product_data(),
            "solution_type": [solution],
            "applied_milestones": list(selections),
        },
    )


@pytest.mark.parametrize(
    ("solution", "selections", "saved"),
    [
        (
            "insurance",
            ["ABDM:m4", "NHCX:nhcx_payer"],
            ["ABDM:m4", "NHCX:nhcx_payer", "ABDM:m1", "ABDM:m3"],
        ),
        (
            "hmis",
            ["ABDM:m4", "UHI:uhi1", "NHCX:nhcx_provider"],
            [
                "ABDM:m4",
                "UHI:uhi1",
                "NHCX:nhcx_provider",
                "ABDM:m1",
                "ABDM:m2",
                "ABDM:m3",
            ],
        ),
        (
            "hmis",
            ["NHCX:nhcx_payer"],
            ["NHCX:nhcx_payer", "ABDM:m1", "ABDM:m2", "ABDM:m3"],
        ),
        (
            "phr",
            ["UHI:uhi1", "NHCX:nhcx_patient_app"],
            ["UHI:uhi1", "NHCX:nhcx_patient_app", "PHR:p1", "PHR:p2", "PHR:p3"],
        ),
        (
            "health_locker",
            ["NHCX:nhcx_patient_app"],
            ["NHCX:nhcx_patient_app", "PHR:p1", "PHR:p2", "PHR:p3", "PHR:p4"],
        ),
    ],
)
def test_what_the_page_posts_after_a_switch_saves(solution, selections, saved):
    form = posted(solution, *selections)

    assert form.is_valid(), form.errors
    assert form.cleaned_data["applied_milestones"] == saved


@pytest.mark.parametrize(
    ("solution", "selections", "error"),
    [
        # Without the script: a provider kept after switching to Insurance.
        (
            "insurance",
            ["NHCX:nhcx_provider"],
            "Select M1 and M2 before Claims exchange as a provider.",
        ),
        # A payer kept after switching to a PHR type.
        (
            "phr",
            ["ABDM:m4", "NHCX:nhcx_payer"],
            "Select M1 and M3 before Claims exchange as a payer.",
        ),
        # The patient app kept after switching to an ABDM type.
        (
            "hmis",
            ["NHCX:nhcx_patient_app"],
            "Select P1 before Claim updates in a patient app.",
        ),
    ],
)
def test_a_role_left_behind_by_a_switch_is_refused(solution, selections, error):
    form = posted(solution, *selections)

    assert not form.is_valid()
    assert form.errors["applied_milestones"] == [error]


def test_a_refused_switch_redraws_the_picker_as_the_type_leaves_it():
    """After the error, the page shows the PHR type's state, not the post's."""
    form = posted("phr", "ABDM:m4", "NHCX:nhcx_payer")
    assert not form.is_valid()

    rows = milestone_rows(form)
    assert [code for code, row in rows.items() if row["selected"]] == [
        "P1",
        "P2",
        "P3",
    ]
    assert not rows["M4"]["selected"]
    assert rows["Payer"]["hidden"]
    assert rows["Provider"]["hidden"]
    assert not rows["Patient app"]["hidden"]


@pytest.mark.django_db
def test_an_edit_adds_and_drops_m4_under_a_fixed_type(environment, client):
    """The type is locked on edit and M1 to M3 post nothing; M4 is free."""
    product, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data={
            **product_data("Ward system"),
            "solution_type": ["hmis"],
            "applied_milestones": [],
        },
    )
    assert product, form.errors
    assert product.applied_milestones == ["ABDM:m1", "ABDM:m2", "ABDM:m3"]
    client.force_login(environment["applicant"])
    url = reverse("experiences:product-edit", args=[product.reference])
    data = product_data("Ward system")
    data.pop("applied_milestones")

    def edit(*selections):
        item = product.review_items.get(kind="product_registration")
        response = client.post(
            url,
            {
                **data,
                "applied_milestones": list(selections),
                "revision": str(item.selected_submission_id or ""),
                "intent": "submit",
            },
        )
        assert response.status_code == 302
        product.refresh_from_db()
        return product.applied_milestones

    assert edit("ABDM:m4", "NHCX:nhcx_provider") == [
        "ABDM:m4",
        "NHCX:nhcx_provider",
        "ABDM:m1",
        "ABDM:m2",
        "ABDM:m3",
    ]
    assert product.milestones.get(key="m4").enabled
    assert edit() == ["ABDM:m1", "ABDM:m2", "ABDM:m3"]
    assert not product.milestones.get(key="m4").enabled
    assert not product.milestones.get(key="nhcx_provider").enabled


def solution_type_box(html, value):
    """The radio for one solution type, as the edit page rendered it."""
    return next(
        field
        for field in Inputs(html).fields
        if field.get("name") == "solution_type" and field.get("value") == value
    )


@pytest.mark.django_db
def test_an_imported_gap_is_shown_and_opens_the_solution_type_again(
    environment,
    client,
):
    """A product the import registered as Other can still be given its real type."""
    product = environment["product"]
    registered_as_other(product)
    product.metadata = {legacy.LEGACY_GAPS: [legacy.SOLUTION_TYPE, legacy.NHCX_ROLE]}
    product.save(update_fields=["metadata"])
    client.force_login(environment["applicant"])
    url = reverse("experiences:product-edit", args=[product.reference])

    html = client.get(url).content.decode()
    assert legacy.GAP_NOTICES[legacy.SOLUTION_TYPE] in html
    assert legacy.GAP_NOTICES[legacy.NHCX_ROLE] in html
    assert legacy.GAP_NOTICES[legacy.TRACKS] not in html
    assert "disabled" not in solution_type_box(html, "hmis")

    legacy.forget_gaps(product, [legacy.SOLUTION_TYPE, legacy.NHCX_ROLE])
    html = client.get(url).content.decode()
    assert legacy.GAP_NOTICES[legacy.SOLUTION_TYPE] not in html
    assert "disabled" in solution_type_box(html, "hmis")


@pytest.mark.django_db
def test_the_other_box_answers_the_gap_in_the_integrators_own_words(
    environment,
    client,
):
    """The note the import left is not an answer; what is written over it is."""
    product = environment["product"]
    registered_as_other(product)
    product.metadata = {legacy.LEGACY_GAPS: [legacy.SOLUTION_TYPE]}
    product.save(update_fields=["metadata"])
    client.force_login(environment["applicant"])
    url = reverse("experiences:product-edit", args=[product.reference])

    def save(note):
        item = product.review_items.get(kind="product_registration")
        response = client.post(
            url,
            {
                **product_data(product.name),
                "solution_type": ["other"],
                "applied_milestones": product.applied_milestones,
                "revision": str(item.selected_submission_id or ""),
                "intent": "submit",
                "solution_type_other": note,
            },
        )
        assert response.status_code == 302, response.context["form"].errors
        product.refresh_from_db()

    save(legacy.UNRECORDED_SOLUTION_TYPE)

    assert legacy.has_gap(product, legacy.SOLUTION_TYPE)

    save("Front desk queue system")

    assert not legacy.gaps(product)
    assert "disabled" in solution_type_box(client.get(url).content.decode(), "hmis")


def reject_registration(product):
    """Put the product's registration in the state the import gives a rejection."""
    registration = product.registration
    registration.status = ReviewItem.Status.REJECTED
    registration.save(update_fields=["status"])


@pytest.mark.django_db
def test_an_unanswered_gap_holds_every_milestone_submission(environment, client):
    """What the product is comes before any evidence about it."""
    product = environment["product"]
    product.metadata = {legacy.LEGACY_GAPS: [legacy.SOLUTION_TYPE]}
    product.save(update_fields=["metadata"])
    client.force_login(environment["applicant"])
    url = _track_url(product, "m1")

    html = client.get(url).content.decode()

    assert "disabled" in submit_button(html)
    assert "Confirm your product details before submitting." in html

    with pytest.raises(
        ValidationError,
        match=r"Confirm your product details before submitting\.",
    ):
        submit(environment, "m1")

    legacy.forget_gaps(product, [legacy.SOLUTION_TYPE])
    html = client.get(url).content.decode()

    assert "disabled" not in submit_button(html)
    assert "Confirm your product details" not in html


@pytest.mark.django_db
def test_a_gap_on_a_rejected_registration_asks_for_a_support_ticket(
    environment,
    client,
):
    """Nobody can confirm details on a registration that cannot be edited."""
    product = environment["product"]
    product.metadata = {legacy.LEGACY_GAPS: [legacy.SOLUTION_TYPE]}
    product.save(update_fields=["metadata"])
    reject_registration(product)
    client.force_login(environment["applicant"])

    html = client.get(_track_url(product, "m1")).content.decode()

    assert "disabled" in submit_button(html)
    assert "Your product registration was rejected" in html
    assert "Raise a support ticket" in html
