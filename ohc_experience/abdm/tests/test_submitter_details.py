"""An organisation verification shows the reviewer who sent it, and flags an
address that is not on the organisation's website domain."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from ohc_experience.abdm.demo import organisation_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.experiences import workflows
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.users.tests.factories import ReviewerFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

FLAG = "Not on the website's domain"


def submitted_verification(email, entity_type="private_company", **data):
    applicant = UserFactory(
        email=email,
        name="Priya Nair",
        phone_number="+919876543210",
    )
    organisation = Organisation.objects.create(
        name="Medibase Technologies",
        entity_type=entity_type,
    )
    Membership.objects.create(organisation=organisation, user=applicant, role="owner")
    item, form, saved = workflows.save_review_form(
        workflows.organisation_review(organisation, applicant),
        applicant,
        data={**organisation_data(), "entity_type": entity_type, **data},
        files={
            "supporting_document": SimpleUploadedFile("cin.pdf", b"%PDF-1.4\n%%EOF"),
        },
        submit=True,
    )
    assert saved, form.errors
    return item


def submitted_by(client, item):
    client.force_login(ReviewerFactory())
    html = client.get(item.get_absolute_url()).content.decode()
    return html.split("Submitted by</dt>", 1)[1].split("</dd>", 1)[0]


def test_the_reviewer_sees_who_sent_the_verification(client):
    row = submitted_by(client, submitted_verification("priya@example.org"))

    assert "Priya Nair" in row
    assert 'href="mailto:priya@example.org"' in row
    assert "+919876543210" in row
    assert FLAG not in row


def test_an_address_on_a_subdomain_of_the_website_is_not_flagged(client):
    row = submitted_by(client, submitted_verification("priya@mail.example.org"))

    assert FLAG not in row


def test_an_address_off_the_website_domain_is_flagged(client):
    row = submitted_by(client, submitted_verification("priya@gmail.com"))

    assert FLAG in row
    assert "The website's domain is example.org." in row


def test_the_product_page_shows_who_sent_the_verification_and_flags_it(client):
    item = submitted_verification("priya@gmail.com")
    workspace, form = workflows.register_product(
        item.organisation,
        item.selected_submission.submitted_by,
        data=product_data(),
    )
    assert workspace, form.errors
    client.force_login(ReviewerFactory())

    html = client.get(
        reverse("experiences:product-detail", args=[workspace.reference]),
    ).content.decode()
    row = html.split("Submitted by</dt>", 1)[1].split("</dd>", 1)[0]

    assert 'href="mailto:priya@gmail.com"' in row
    assert FLAG in row


def test_a_sole_proprietorship_is_not_flagged_for_a_personal_address(client):
    item = submitted_verification(
        "anita@gmail.com",
        entity_type="sole_proprietor",
        verification_document_type="PAN",
    )

    assert FLAG not in submitted_by(client, item)
