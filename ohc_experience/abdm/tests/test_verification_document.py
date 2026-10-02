"""The verification document is optional: its type, number and file alike."""

import pytest

from ohc_experience.abdm.demo import organisation_data
from ohc_experience.abdm.forms import OrganisationForm
from ohc_experience.experiences import workflows
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_the_document_type_starts_unchosen():
    select = str(OrganisationForm()["verification_document_type"])

    assert '<option value="" selected>Select a document type</option>' in select


def test_the_document_upload_states_what_it_takes():
    """The picker and the hint say the same thing the validator enforces."""
    field = OrganisationForm().fields["supporting_document"]

    assert field.widget.attrs["accept"] == ".pdf"
    assert field.help_text == "PDF, up to 10 MB."


def test_an_organisation_submits_without_a_verification_document():
    applicant = UserFactory()
    organisation = Organisation.objects.create(name="Medibase Technologies")
    Membership.objects.create(organisation=organisation, user=applicant, role="owner")

    _, form, saved = workflows.save_review_form(
        workflows.organisation_review(organisation, applicant),
        applicant,
        data={
            **organisation_data(),
            "verification_document_type": "",
            "verification_document_number": "",
        },
        submit=True,
    )

    assert saved, form.errors
