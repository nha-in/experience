"""The vendor-facing events pages: the list, and one event in full."""

from __future__ import annotations

import re
from datetime import UTC
from datetime import datetime
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from django.utils import timezone

from ohc_experience.events.tests.factories import EventFactory
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from ohc_experience.events.models import Event
    from ohc_experience.organisations.models import Membership
    from ohc_experience.users.models import User

pytestmark = pytest.mark.django_db


class TestEventListView:
    def test_requires_login(self, client: Client):
        response = client.get(reverse("events:list"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"].startswith(reverse("account_login"))

    def test_lists_published_upcoming_events(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        event = EventFactory.create(published=True, title="Care v3.3 upgrade webinar")

        response = sign_in(owner_membership.user).get(reverse("events:list"))

        assert response.status_code == HTTPStatus.OK
        assert response.context["nav_section"] == "events"
        assert list(response.context["upcoming_events"]) == [event]
        assert "Care v3.3 upgrade webinar" in response.content.decode()

    def test_hides_an_unpublished_event(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        EventFactory.create(title="Draft roadmap AMA")

        response = sign_in(owner_membership.user).get(reverse("events:list"))

        assert list(response.context["upcoming_events"]) == []
        assert list(response.context["past_events"]) == []
        assert "Draft roadmap AMA" not in response.content.decode()

    def test_a_finished_event_is_past_and_not_upcoming(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        finished = EventFactory.create(
            published=True,
            past=True,
            title="Certification AMA",
        )
        ahead = EventFactory.create(published=True, title="Partner office hours")

        response = sign_in(owner_membership.user).get(reverse("events:list"))

        assert list(response.context["upcoming_events"]) == [ahead]
        assert list(response.context["past_events"]) == [finished]

    def test_the_sidebar_marks_events_as_the_current_section(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        html = (
            sign_in(owner_membership.user).get(reverse("events:list")).content.decode()
        )
        nav = html[html.index('<nav id="app-nav"') : html.index("</nav>")]

        assert reverse("events:list") in nav
        assert "ui-nav-link--active" in nav
        assert 'aria-current="page"' in nav

    def test_says_plainly_when_nothing_is_scheduled(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).get(reverse("events:list"))

        assert list(response.context["upcoming_events"]) == []
        assert "Nothing is scheduled right now" in response.content.decode()


class TestEventDetailView:
    def test_requires_login(self, client: Client):
        event = EventFactory.create(published=True)

        response = client.get(reverse("events:detail", kwargs={"slug": event.slug}))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"].startswith(reverse("account_login"))

    def test_renders_a_published_event(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        event = EventFactory.create(
            published=True,
            title="Care v3.3 upgrade webinar",
            join_url="https://meet.ohc.network/v33",
        )

        response = sign_in(owner_membership.user).get(event.get_absolute_url())

        assert response.status_code == HTTPStatus.OK
        assert response.context["event"] == event
        assert response.context["nav_section"] == "events"
        body = response.content.decode()
        assert "Care v3.3 upgrade webinar" in body
        assert "https://meet.ohc.network/v33" in body

    def test_an_unpublished_slug_is_a_404(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        # A draft has a real, guessable slug — it still must not exist here.
        event = EventFactory.create(title="Draft roadmap AMA")

        response = sign_in(owner_membership.user).get(
            reverse("events:detail", kwargs={"slug": event.slug}),
        )

        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_an_unknown_slug_is_a_404(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).get(
            reverse("events:detail", kwargs={"slug": "no-such-event"}),
        )

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestOnlyOhcCanPublish:
    """The vendor pages are read-only, and the gate on publishing holds.

    An event is visible to a vendor for exactly one reason — the OHC team
    published it. So the interesting question is not only "does a draft 404",
    but "can a vendor make a draft stop being a draft". Both are asked here
    against real requests rather than by reading the mixin list.
    """

    def test_the_list_refuses_a_post(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).post(reverse("events:list"))

        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED

    def test_an_event_refuses_a_post(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        event = EventFactory.create(published=True)

        response = sign_in(owner_membership.user).post(event.get_absolute_url())

        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED

    def test_a_vendor_is_refused_the_ohc_events_console(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).get(reverse("ohc:events"))

        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_a_vendor_post_cannot_publish_a_draft(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        draft = EventFactory.create(title="Draft roadmap AMA")
        client = sign_in(owner_membership.user)

        response = client.post(
            reverse("ohc:event-publish", kwargs={"slug": draft.slug}),
        )

        assert response.status_code == HTTPStatus.FORBIDDEN
        draft.refresh_from_db()
        assert draft.published_at is None
        # And the refusal actually held: the draft is still not readable.
        assert client.get(draft.get_absolute_url()).status_code == HTTPStatus.NOT_FOUND

    def test_a_vendor_post_cannot_unpublish_a_live_event(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        event = EventFactory.create(published=True, title="Care v3.3 upgrade webinar")
        client = sign_in(owner_membership.user)

        response = client.post(
            reverse("ohc:event-publish", kwargs={"slug": event.slug}),
        )

        assert response.status_code == HTTPStatus.FORBIDDEN
        event.refresh_from_db()
        assert event.published_at is not None

    def test_a_vendor_post_cannot_edit_an_event(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        event = EventFactory.create(published=True, title="Care v3.3 upgrade webinar")

        response = sign_in(owner_membership.user).post(
            reverse("ohc:event-update", kwargs={"slug": event.slug}),
            {"title": "Owned", "starts_at": "2030-01-01 10:00:00", "kind": "webinar"},
        )

        assert response.status_code == HTTPStatus.FORBIDDEN
        event.refresh_from_db()
        assert event.title == "Care v3.3 upgrade webinar"

    def test_the_same_post_publishes_when_the_ohc_team_sends_it(
        self,
        sign_in: Callable[[User], Client],
    ):
        """The control for the two tests above.

        Without this they would still pass if the publish route were broken or
        forbidden to everyone, and "vendors are refused" would mean nothing.
        """
        draft = EventFactory.create(title="Draft roadmap AMA")
        staffer = UserFactory.create(email="ops@ohc.network", is_ohc_team=True)

        response = sign_in(staffer).post(
            reverse("ohc:event-publish", kwargs={"slug": draft.slug}),
        )

        assert response.status_code == HTTPStatus.FOUND
        draft.refresh_from_db()
        assert draft.published_at is not None
        # And now the vendor page that 404'd a moment ago serves it.
        assert (
            sign_in(UserFactory.create()).get(draft.get_absolute_url()).status_code
            == HTTPStatus.OK
        )


class TestEventsAreNotScopedToAnOrganisation:
    """Published events belong to every vendor, and drafts to none of them."""

    def test_a_second_organisation_sees_the_very_same_event(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        event = EventFactory.create(published=True, title="Care v3.3 upgrade webinar")
        other = MembershipFactory.create(
            organisation=OrganisationFactory.create(
                name="Meridian Care",
                onboarded=True,
            ),
            role="owner",
        )

        for membership in (owner_membership, other):
            response = sign_in(membership.user).get(reverse("events:list"))

            assert response.status_code == HTTPStatus.OK
            assert list(response.context["upcoming_events"]) == [event]

    def test_a_draft_is_a_404_for_every_organisation(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        draft = EventFactory.create(title="Draft roadmap AMA")
        other = MembershipFactory.create(
            organisation=OrganisationFactory.create(
                name="Meridian Care",
                onboarded=True,
            ),
            role="owner",
        )

        for membership in (owner_membership, other):
            response = sign_in(membership.user).get(draft.get_absolute_url())

            assert response.status_code == HTTPStatus.NOT_FOUND

    def test_a_signed_in_user_with_no_organisation_still_reads_events(
        self,
        sign_in: Callable[[User], Client],
        user: User,
    ):
        """Membership is not the gate here — being signed in is.

        An OHC staffer belongs to no vendor organisation; 403-ing them off a
        page the OHC team wrote would be absurd.
        """
        event = EventFactory.create(published=True, title="Care v3.3 upgrade webinar")

        response = sign_in(user).get(reverse("events:list"))

        assert response.status_code == HTTPStatus.OK
        assert list(response.context["upcoming_events"]) == [event]


def collapse(html: str) -> str:
    """Template indentation is not content — match on the words alone."""
    return re.sub(r"\s+", " ", html)


def main_content(html: str) -> str:
    """Just the part of the shell these pages own."""
    start = html.index('<main id="main-content"')
    return html[start : html.index("</main>", start)]


class TestWorksWithoutJavaScript:
    """No script needed: every link is a real href and registration is a real POST.

    The requests below carry no HX-Request header, which is exactly what a
    browser with scripting off sends, so what these assert is the no-JS path.
    """

    def test_the_list_is_plain_html(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        event = EventFactory.create(published=True, join_url="https://meet.ohc.test/x")
        EventFactory.create(published=True, past=True)

        body = main_content(
            sign_in(owner_membership.user).get(reverse("events:list")).content.decode(),
        )

        assert "<script" not in body
        # The title link and the Join link are both real destinations.
        assert f'href="{event.get_absolute_url()}"' in body
        assert 'href="https://meet.ohc.test/x"' in body
        # Registering is a real POST to a real URL; htmx only enhances it.
        register_url = reverse("events:registration", args=[event.slug, "register"])
        assert f'action="{register_url}"' in body
        assert body.count("csrfmiddlewaretoken") == body.count('method="post"')
        # Past events collapse with the browser's own widget, not a script.
        assert "<details" in body

    def test_an_event_page_is_plain_html(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        event = EventFactory.create(published=True, join_url="https://meet.ohc.test/x")

        body = main_content(
            sign_in(owner_membership.user)
            .get(event.get_absolute_url())
            .content.decode(),
        )

        assert "<script" not in body
        assert f'href="{reverse("events:list")}"' in body
        register_url = reverse("events:registration", args=[event.slug, "register"])
        assert f'action="{register_url}"' in body

    def test_the_dashboard_card_rows_are_real_links(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
        product,
    ):
        event = EventFactory.create(published=True)

        body = (
            sign_in(owner_membership.user)
            .get(product.get_absolute_url())
            .content.decode()
        )

        assert f'href="{event.get_absolute_url()}"' in body
        assert f'href="{reverse("events:list")}"' in body

    @pytest.mark.parametrize("path_name", ["events:list", "events:detail"])
    def test_signed_out_gets_a_real_redirect_not_an_htmx_one(
        self,
        client: Client,
        path_name: str,
    ):
        """A 302 with a Location header, which a scriptless browser follows.

        allauth can answer htmx with an HX-Redirect header instead; a request
        without HX-Request must not get that, or signed-out users with
        scripting off would sit on a blank page.
        """
        event = EventFactory.create(published=True)
        url = (
            reverse(path_name)
            if path_name == "events:list"
            else reverse(path_name, kwargs={"slug": event.slug})
        )

        response = client.get(url)

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == f"{reverse('account_login')}?next={url}"
        assert "HX-Redirect" not in response


class TestTheTimeIsUnambiguous:
    """An event time with no zone on it is a wrong time.

    Every other timestamp in this app is something that already happened, where
    the zone does not much matter. These are times a vendor has to show up for,
    and the app renders in IST, so the zone is printed rather than assumed —
    a partner reading from abroad must not guess.
    """

    @pytest.fixture
    def event(self) -> Event:
        return EventFactory.create(
            published=True,
            title="Care v3.3 upgrade webinar",
            starts_at=datetime(2099, 9, 14, 15, 0, tzinfo=UTC),
        )

    def test_the_list_names_the_zone(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
        event: Event,
    ):
        body = collapse(
            sign_in(owner_membership.user).get(reverse("events:list")).content.decode(),
        )

        assert "20:30 IST" in body

    def test_an_event_page_names_the_zone(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
        event: Event,
    ):
        body = collapse(
            sign_in(owner_membership.user)
            .get(event.get_absolute_url())
            .content.decode(),
        )

        assert "20:30 IST" in body

    def test_the_dashboard_card_names_the_zone(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
        event: Event,
        product,
    ):
        body = collapse(
            sign_in(owner_membership.user)
            .get(product.get_absolute_url())
            .content.decode(),
        )

        assert "20:30 IST" in body

    def test_the_zone_follows_the_setting_rather_than_being_hardcoded(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
        event: Event,
        settings,
    ):
        settings.TIME_ZONE = "Asia/Kolkata"
        timezone.activate("Asia/Kolkata")
        try:
            body = collapse(
                sign_in(owner_membership.user)
                .get(reverse("events:list"))
                .content.decode(),
            )
        finally:
            timezone.deactivate()

        assert "20:30 IST" in body
        assert "UTC" not in main_content(body)
