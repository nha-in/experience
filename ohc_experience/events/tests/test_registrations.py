"""Event registration, the filter chips, past materials and reminders."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from ohc_experience.events.models import Event
from ohc_experience.events.models import EventRegistration
from ohc_experience.events.tasks import send_event_reminders
from ohc_experience.events.tests.factories import EventFactory

pytestmark = pytest.mark.django_db


def registration_url(event: Event, action: str) -> str:
    return reverse("events:registration", args=[event.slug, action])


class TestRegistration:
    def test_registering_confirms_by_email_and_shows_on_the_list(
        self,
        sign_in,
        owner_membership,
    ):
        event = EventFactory.create(published=True, title="HI-CM M2 office hours")
        client = sign_in(owner_membership.user)

        response = client.post(registration_url(event, "register"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == event.get_absolute_url()
        assert EventRegistration.objects.filter(
            event=event,
            user=owner_membership.user,
        ).exists()
        assert mail.outbox[-1].to == [owner_membership.user.email]
        assert "Registered: HI-CM M2 office hours" in mail.outbox[-1].subject
        html = client.get(reverse("events:list")).content.decode()
        assert "Registered" in html
        assert registration_url(event, "withdraw") in html

    def test_registering_twice_is_harmless(self, sign_in, owner_membership):
        event = EventFactory.create(published=True)
        client = sign_in(owner_membership.user)

        client.post(registration_url(event, "register"))
        client.post(registration_url(event, "register"))

        assert EventRegistration.objects.filter(event=event).count() == 1
        assert len(mail.outbox) == 1

    def test_an_htmx_withdrawal_swaps_the_control(self, sign_in, owner_membership):
        event = EventFactory.create(published=True)
        EventRegistration.objects.create(event=event, user=owner_membership.user)

        response = sign_in(owner_membership.user).post(
            registration_url(event, "withdraw"),
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert f'id="registration-{event.pk}"' in html
        assert registration_url(event, "register") in html
        assert "Registration withdrawn." in html
        assert not EventRegistration.objects.filter(event=event).exists()

    def test_a_draft_event_cannot_be_registered_for(self, sign_in, owner_membership):
        event = EventFactory.create()

        response = sign_in(owner_membership.user).post(
            registration_url(event, "register"),
        )

        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_a_finished_event_offers_materials_not_registration(
        self,
        sign_in,
        owner_membership,
    ):
        event = EventFactory.create(
            published=True,
            past=True,
            materials_url="https://docs.example.test/slides.pdf",
            recording_url="https://video.example.test/rec",
        )
        client = sign_in(owner_membership.user)

        html = client.get(event.get_absolute_url()).content.decode()
        assert "https://docs.example.test/slides.pdf" in html
        assert "Recording" in html
        assert registration_url(event, "register") not in html

        response = client.post(registration_url(event, "register"))
        assert response.status_code == HTTPStatus.FOUND
        assert not EventRegistration.objects.filter(event=event).exists()


class TestChips:
    def test_registered_chip_narrows_to_my_events(self, sign_in, owner_membership):
        mine = EventFactory.create(published=True, title="Mine")
        other = EventFactory.create(published=True, title="Other")
        EventRegistration.objects.create(event=mine, user=owner_membership.user)

        response = sign_in(owner_membership.user).get(
            reverse("events:list"),
            {"chip": "registered"},
        )

        assert list(response.context["upcoming_events"]) == [mine]
        assert other.title not in response.content.decode()

    def test_a_kind_chip_filters_and_an_unknown_one_is_ignored(
        self,
        sign_in,
        owner_membership,
    ):
        webinar = EventFactory.create(published=True, kind=Event.Kind.WEBINAR)
        EventFactory.create(published=True, kind=Event.Kind.AMA)
        client = sign_in(owner_membership.user)

        webinars = client.get(reverse("events:list"), {"chip": "webinar"})
        assert list(webinars.context["upcoming_events"]) == [webinar]
        unknown = client.get(reverse("events:list"), {"chip": "junk"})
        assert unknown.context["chip"] == ""


class TestReminders:
    def test_registrants_are_reminded_once_the_day_before(self, owner_membership):
        soon = EventFactory.create(
            published=True,
            starts_at=timezone.now() + timedelta(hours=20),
        )
        later = EventFactory.create(
            published=True,
            starts_at=timezone.now() + timedelta(days=3),
        )
        registration = EventRegistration.objects.create(
            event=soon,
            user=owner_membership.user,
        )
        EventRegistration.objects.create(event=later, user=owner_membership.user)

        assert send_event_reminders() == 1
        registration.refresh_from_db()
        assert registration.reminded_at is not None
        assert "Tomorrow:" in mail.outbox[-1].subject
        assert send_event_reminders() == 0
