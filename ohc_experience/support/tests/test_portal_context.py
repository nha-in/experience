"""Tickets carry product and track context, an SLA, attachments and knowledge."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.support.knowledge import related_knowledge
from ohc_experience.support.models import Category
from ohc_experience.support.models import Priority
from ohc_experience.support.models import Ticket
from ohc_experience.support.models import post_reply
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def upload(name="trace.log", body=b"401 from /v3/sessions"):
    return SimpleUploadedFile(name, body, content_type="text/plain")


@pytest.fixture
def reviewer(db):
    return UserFactory.create(email="desk@nha.gov.in", is_ohc_team=True)


class TestSla:
    def test_severity_sets_the_first_response_commitment(self, organisation):
        high = Ticket(organisation=organisation, priority=Priority.HIGH)
        low = Ticket(organisation=organisation, priority=Priority.LOW)

        assert high.sla_label == "First response within 1 business day"
        assert low.sla_label == "First response within 5 business days"
        assert high.sla_met is None

    def test_sla_is_met_or_missed_by_the_first_reply(self, organisation, reviewer):
        ticket = Ticket.objects.create(
            organisation=organisation,
            subject="Slow",
            priority=Priority.HIGH,
        )
        Ticket.objects.filter(pk=ticket.pk).update(
            created_at=timezone.now() - timedelta(days=3),
        )
        ticket.refresh_from_db()

        post_reply(ticket, reviewer, "On it.", from_ohc_team=True, notify=False)

        assert ticket.sla_met is False


class TestTicketContext:
    def test_creating_a_ticket_with_product_track_and_attachment(
        self,
        sign_in,
        owner_membership,
        product,
        reviewer,
    ):
        response = sign_in(owner_membership.user).post(
            reverse("support:create"),
            data={
                "subject": "Callback never received",
                "category": Category.API,
                "priority": Priority.HIGH,
                "product": product.pk,
                "track": "HI-CM",
                "linked_facility": "",
                "body": "We link a care context and no callback arrives.",
                "attachment": upload(),
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        ticket = Ticket.objects.get()
        assert ticket.product == product
        assert ticket.track == "HI-CM"
        assert ticket.context_label == f"{product.name} · HI-CM"
        message = ticket.messages.get()
        assert message.attachment_name == "trace.log"
        # The desk hears about the new ticket.
        assert mail.outbox[-1].to == [reviewer.email]
        assert ticket.reference in mail.outbox[-1].subject

        html = (
            sign_in(owner_membership.user)
            .get(ticket.get_absolute_url())
            .content.decode()
        )
        assert product.name in html
        assert "HI-CM" in html
        assert "First response within 1 business day" in html
        assert "Related knowledge" in html
        assert "Gateway API reference" in html
        download = reverse(
            "products:document",
            args=["ticket-message", message.pk, "attachment"],
        )
        assert download in html

    def test_the_product_picker_only_lists_this_organisations_products(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        outsider = MembershipFactory.create(role=Role.OWNER)

        response = sign_in(outsider.user).get(reverse("support:create"))

        assert product.pk not in [
            item.pk for item in response.context["form"].fields["product"].queryset
        ]

    def test_an_unsupported_attachment_type_is_refused(self, sign_in, owner_membership):
        response = sign_in(owner_membership.user).post(
            reverse("support:create"),
            data={
                "subject": "Bad file",
                "category": Category.API,
                "priority": Priority.LOW,
                "body": "See file.",
                "attachment": upload("payload.exe", b"MZ"),
            },
        )

        assert response.status_code == HTTPStatus.OK
        assert "file types" in response.content.decode()
        assert not Ticket.objects.exists()

    def test_a_reply_with_an_attachment_emails_the_vendor(
        self,
        sign_in,
        owner_membership,
        reviewer,
    ):
        ticket = Ticket.objects.create(
            organisation=owner_membership.organisation,
            subject="Help",
            created_by=owner_membership.user,
        )
        mail.outbox.clear()

        response = sign_in(reviewer).post(
            reverse("ohc:ticket-reply", args=[ticket.reference]),
            data={
                "body": "Here is the trace.",
                "attachment": upload("desk.json", b"{}"),
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        message = ticket.messages.get()
        assert message.from_ohc_team
        assert message.attachment_name == "desk.json"
        assert owner_membership.user.email in mail.outbox[-1].to
        assert "desk.json" in mail.outbox[-1].body

    def test_attachments_download_only_for_the_organisation_and_reviewers(
        self,
        sign_in,
        owner_membership,
        reviewer,
    ):
        ticket = Ticket.objects.create(
            organisation=owner_membership.organisation,
            subject="Help",
            created_by=owner_membership.user,
        )
        message = post_reply(
            ticket,
            owner_membership.user,
            "See attached.",
            from_ohc_team=False,
            attachment=upload(),
            notify=False,
        )
        url = reverse(
            "products:document",
            args=["ticket-message", message.pk, "attachment"],
        )

        assert sign_in(owner_membership.user).get(url).status_code == HTTPStatus.OK
        assert sign_in(reviewer).get(url).status_code == HTTPStatus.OK
        outsider = MembershipFactory.create(role=Role.OWNER)
        assert sign_in(outsider.user).get(url).status_code == HTTPStatus.FORBIDDEN


class TestRelatedKnowledge:
    def test_links_hang_off_the_configured_docs_base(self, settings):
        settings.ABDM_DOCS_URL = "https://docs.example.test"

        entries = related_knowledge(Category.CERTIFICATION)

        assert entries[0]["url"].startswith("https://docs.example.test/")
        assert any("exit" in entry["url"] for entry in entries)
