from http import HTTPStatus
from importlib import import_module

import pytest
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from ohc_experience.abdm.demo import organisation_data
from ohc_experience.abdm.forms import OrganisationForm
from ohc_experience.experiences import workflows
from ohc_experience.organisations.models import Organisation

pytestmark = pytest.mark.django_db


def verification_document():
    pdf = SimpleUploadedFile("pan.pdf", b"%PDF-1.4\n%%EOF")
    return {"supporting_document": pdf}


def sole_proprietorship_data(**overrides):
    return {
        **organisation_data(),
        "entity_type": "sole_proprietor",
        "verification_document_type": "PAN",
        **overrides,
    }


def sign_up(client, entity_type, name="Rao Health Apps"):
    response = client.post(
        reverse("account_signup"),
        data={
            "name": "Anita Rao",
            "email": f"{entity_type}@example.org",
            "mobile_number": "9876543210",
            "organisation": name,
            "organisation_type": entity_type,
            "website": "https://rao.example",
            "password1": "sandbox-Kerala-2026",
            "password2": "sandbox-Kerala-2026",
        },
    )
    assert response.status_code == HTTPStatus.FOUND
    return Organisation.objects.get(name=name)


def test_a_sole_proprietorship_verifies_with_pan_or_gstin_not_cin(lgd_lookup):
    with_cin = OrganisationForm(
        sole_proprietorship_data(verification_document_type="CIN"),
        files=verification_document(),
    )
    with_pan = OrganisationForm(
        sole_proprietorship_data(),
        files=verification_document(),
    )

    assert with_cin.errors["verification_document_type"] == [
        "An individual or sole proprietorship has no CIN. Choose PAN or GSTIN.",
    ]
    assert with_pan.is_valid(), with_pan.errors


def test_signup_saves_the_type_and_the_organisation_form_starts_from_it(client):
    organisation = sign_up(client, "sole_proprietor")
    item = workflows.organisation_review(organisation, organisation.owner)

    assert organisation.entity_type == "sole_proprietor"
    assert workflows.build_form(item)["entity_type"].value() == "sole_proprietor"


def test_the_organisation_form_shows_the_type_but_will_not_change_it(
    client,
    lgd_lookup,
):
    organisation = sign_up(client, "private_company")
    item = workflows.organisation_review(organisation, organisation.owner)

    assert workflows.build_form(item)["entity_type"].field.disabled

    _, form, saved = workflows.save_review_form(
        item,
        organisation.owner,
        data=sole_proprietorship_data(),
        files=verification_document(),
        submit=True,
    )

    assert saved, form.errors
    assert form.cleaned_data["entity_type"] == "private_company"
    organisation.refresh_from_db()
    assert organisation.entity_type == "private_company"


def test_an_organisation_with_no_type_yet_still_chooses_one_here(client, lgd_lookup):
    # A social sign-in never asked, and neither did an account older than the
    # field. The first submission settles it, and locks it from then on.
    organisation = sign_up(client, "private_company")
    Organisation.objects.update(entity_type="")
    organisation.refresh_from_db()
    item = workflows.organisation_review(organisation, organisation.owner)

    assert not workflows.build_form(item)["entity_type"].field.disabled

    _, form, saved = workflows.save_review_form(
        item,
        organisation.owner,
        data=sole_proprietorship_data(),
        files=verification_document(),
        submit=True,
    )

    assert saved, form.errors
    organisation.refresh_from_db()
    assert organisation.entity_type == "sole_proprietor"
    assert workflows.build_form(item)["entity_type"].field.disabled


def test_only_a_sole_proprietorship_may_leave_the_website_out(client, lgd_lookup):
    individual = sign_up(client, "sole_proprietor")
    company = sign_up(Client(), "private_company", name="Sunrise Health Systems")

    def submit(organisation):
        return workflows.save_review_form(
            workflows.organisation_review(organisation, organisation.owner),
            organisation.owner,
            data=sole_proprietorship_data(name=organisation.name, website=""),
            files=verification_document(),
            submit=True,
        )

    _, individual_form, individual_saved = submit(individual)
    _, company_form, company_saved = submit(company)

    assert individual_saved, individual_form.errors
    assert not company_saved
    assert company_form.errors["website"] == ["This field is required."]


@pytest.mark.parametrize(
    ("entity_type", "noun"),
    [("sole_proprietor", "Business"), ("private_company", "Organisation")],
)
def test_the_organisation_page_names_the_entity_by_its_type(client, entity_type, noun):
    organisation = sign_up(client, entity_type)
    client.force_login(organisation.owner)

    html = client.get(reverse("experiences:organisation")).content.decode()

    assert f"{noun} verification</p>" in html
    assert f"{noun} details</h1>" in html
    assert f"{noun}</a></li>" in html  # the navigation link
    assert f"2. {noun}</li>" in html  # the onboarding step
    assert "data-email-domain-callout" in html


def test_the_migration_fills_the_type_from_the_latest_submission_else_signup(
    client,
    lgd_lookup,
):
    submitted = sign_up(client, "private_company", name="Submitted Health")
    item, _, _ = workflows.save_review_form(
        workflows.organisation_review(submitted, submitted.owner),
        submitted.owner,
        data=sole_proprietorship_data(),
        files=verification_document(),
        submit=True,
    )
    # The type was still the form's to change when these submissions were made,
    # which is the data the migration exists for.
    item.refresh_from_db()
    submission = item.selected_submission
    submission.data["entity_type"] = "sole_proprietor"
    submission.save(update_fields=["data"])
    # A pending verification code owns the session, so the second signup
    # needs its own client.
    signed_up = sign_up(Client(), "government", name="Signed Up Health")
    review = workflows.organisation_review(signed_up, signed_up.owner)
    review.form.metadata["entity_type"] = "government"
    review.form.save(update_fields=["metadata"])
    Organisation.objects.update(entity_type="")

    migration = import_module(
        "ohc_experience.organisations.migrations.0006_organisation_entity_type",
    )
    migration.copy_entity_types(apps, None)

    submitted.refresh_from_db()
    signed_up.refresh_from_db()
    assert submitted.entity_type == "sole_proprietor"
    assert signed_up.entity_type == "government"
