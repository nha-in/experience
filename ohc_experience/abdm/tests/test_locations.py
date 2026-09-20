from http import HTTPStatus
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from ohc_experience.abdm.demo import organisation_data
from ohc_experience.abdm.forms import OrganisationForm
from ohc_experience.experiences import workflows
from ohc_experience.organisations import states
from ohc_experience.organisations.lgd import LGDLookupError


def organisation_form(**overrides):
    data = {**organisation_data(), **overrides}
    return OrganisationForm(
        data,
        files={
            "supporting_document": SimpleUploadedFile(
                "verification.pdf",
                b"%PDF-1.4\n%%EOF",
                content_type="application/pdf",
            ),
        },
    )


def test_unique_pin_fills_names_without_javascript(lgd_lookup):
    form = organisation_form(state="", district="")
    assert form.is_valid(), form.errors
    assert form.cleaned_data["state"] == lgd_lookup[0]["state"]
    assert form.cleaned_data["district"] == lgd_lookup[0]["district"]
    assert form.cleaned_data["state_lgd_code"] == "29"
    assert form.cleaned_data["district_lgd_code"] == "525"


def test_legacy_name_casing_is_canonicalized_and_posted_codes_ignored():
    form = organisation_form(state_lgd_code="99", district_lgd_code="999")
    assert form.is_valid(), form.errors
    assert form.cleaned_data["state"] == "KARNATAKA"
    assert form.cleaned_data["district"] == "BENGALURU URBAN"
    assert form.cleaned_data["state_lgd_code"] == "29"
    assert form.cleaned_data["district_lgd_code"] == "525"


@pytest.mark.parametrize(
    "address",
    [
        {"state": "KERALA"},
        {"district": "ERNAKULAM"},
        {"pincode": "110001"},
    ],
)
def test_tampered_or_stale_address_cannot_be_submitted(address):
    form = organisation_form(**address)
    assert not form.is_valid()
    assert "district" in form.errors or "pincode" in form.errors
    assert not form.cleaned_data["state_lgd_code"]
    assert not form.cleaned_data["district_lgd_code"]


@pytest.mark.parametrize("pincode", ["", "12345", "012345", "560001\n7", "abcdef"])
def test_invalid_pin_never_calls_provider(pincode):
    with patch("ohc_experience.abdm.forms.lookup_pincode") as lookup:
        form = organisation_form(pincode=pincode)
    assert not form.is_valid()
    assert "pincode" in form.errors
    lookup.assert_not_called()


def test_lookup_failure_accepts_a_pair_from_the_offline_list():
    """A provider outage must not hold up onboarding, but earns no LGD codes."""
    with patch(
        "ohc_experience.abdm.forms.lookup_pincode",
        side_effect=LGDLookupError,
    ):
        form = organisation_form()
    assert form.is_valid(), form.errors
    assert form.cleaned_data["state"] == "Karnataka"
    assert form.cleaned_data["district"] == "Bengaluru Urban"
    assert not form.cleaned_data["state_lgd_code"]
    assert not form.cleaned_data["district_lgd_code"]


def test_lookup_failure_still_rejects_a_pair_outside_the_offline_list():
    with patch(
        "ohc_experience.abdm.forms.lookup_pincode",
        side_effect=LGDLookupError,
    ):
        form = organisation_form(state="Kerala", district="BENGALURU URBAN")
    assert not form.is_valid()
    assert "district" in form.errors


def test_a_real_pair_stands_even_where_lgd_answered_differently(lgd_lookup):
    """The person chose it; we do not overrule them. It just earns no codes."""
    form = organisation_form(state="Kerala", district="Ernakulam")
    assert form.is_valid(), form.errors
    assert form.cleaned_data["state"] == "Kerala"
    assert form.cleaned_data["district"] == "Ernakulam"
    assert not form.cleaned_data["state_lgd_code"]
    assert not form.cleaned_data["district_lgd_code"]


def test_lookup_failure_offers_every_state_and_the_districts_of_one():
    with patch(
        "ohc_experience.abdm.forms.lookup_pincode",
        side_effect=LGDLookupError,
    ):
        form = organisation_form(state="Kerala", district="")
    choices = {
        name: [value for value, _ in form.fields[name].widget.choices if value]
        for name in ("state", "district")
    }
    assert len(choices["state"]) == len(states.state_names())
    assert choices["district"] == list(states.district_names("Kerala"))


def test_multiple_districts_require_selection(lgd_lookup):
    locations = [
        *lgd_lookup,
        {**lgd_lookup[0], "district": "SECOND DISTRICT", "district_code": "999"},
    ]
    with patch("ohc_experience.abdm.forms.lookup_pincode", return_value=locations):
        ambiguous = organisation_form(state="", district="")
        selected = organisation_form(state="", district="SECOND DISTRICT")
    assert not ambiguous.is_valid()
    assert "district" in ambiguous.errors
    assert selected.is_valid(), selected.errors
    assert selected.cleaned_data["district_lgd_code"] == "999"


def test_cross_state_district_pair_is_rejected(lgd_lookup):
    locations = [
        *lgd_lookup,
        {
            "state": "SECOND STATE",
            "state_code": "99",
            "district": "SECOND DISTRICT",
            "district_code": "999",
        },
    ]
    with patch("ohc_experience.abdm.forms.lookup_pincode", return_value=locations):
        form = organisation_form(district="SECOND DISTRICT")
    assert not form.is_valid()
    assert "district" in form.errors


def test_initial_address_renders_without_provider_call():
    with patch("ohc_experience.abdm.forms.lookup_pincode") as lookup:
        form = OrganisationForm(initial=organisation_data())
    assert ("Karnataka", "Karnataka") in form.fields["state"].widget.choices
    assert "pincode-lookup.js" in str(form.media)
    lookup.assert_not_called()


@pytest.mark.django_db
def test_organisation_post_persists_lgd_location(client, owner_membership):
    client.force_login(owner_membership.user)
    data = organisation_data()
    data.update(
        state="",
        district="",
        state_lgd_code="999",
        district_lgd_code="999",
        revision="",
        supporting_document=SimpleUploadedFile(
            "verification.pdf",
            b"%PDF-1.4\n%%EOF",
            content_type="application/pdf",
        ),
    )
    response = client.post(reverse("experiences:organisation"), data)
    assert response.status_code == HTTPStatus.FOUND
    item = workflows.organisation_review(
        owner_membership.organisation,
        owner_membership.user,
    )
    stored = item.selected_submission.data
    assert stored["pincode"] == "560001"
    assert stored["state"] == "KARNATAKA"
    assert stored["district"] == "BENGALURU URBAN"
    assert stored["state_lgd_code"] == "29"
    assert stored["district_lgd_code"] == "525"
    visible_fields = {field["key"] for field in item.selected_submission.field_schema}
    assert "state_lgd_code" not in visible_fields
    assert "district_lgd_code" not in visible_fields
    owner_membership.organisation.refresh_from_db()
    assert owner_membership.organisation.state == "KARNATAKA"
    assert owner_membership.organisation.city == "BENGALURU URBAN"
