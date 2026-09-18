"""Support permissions, read through the tickets they are there to scope.

Staff support access is granted per program and per category: a program track
code, "" for work that is not track-specific, or "*" for the lot. A ticket's
category is the other half of that pair, so these tests keep real grants and
real tickets together rather than asserting on the query that joins them.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.urls import reverse

from ohc_experience.experiences import permissions
from ohc_experience.experiences.forms import SupportForm
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductWorkspace
from ohc_experience.experiences.registry import get_program
from ohc_experience.experiences.registry import registry
from ohc_experience.experiences.tests.example_program import SupplierQuality
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.support.models import Ticket
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

TRACKS = ["HIE-CM", "UHI", "NHCX", "PHR", "HealthLocker"]
#: A category no track answers to any more, as a retired or renamed track
#: leaves behind on the tickets that were filed under it.
RETIRED = "Sandbox"


def product_in(program_key, membership, name="Sandbox HMIS"):
    """A registered product, which is what ties a ticket to a program."""
    product = Product.objects.create(
        organisation=membership.organisation,
        name=name,
        description="A product under test.",
        created_by=membership.user,
    )
    ProductWorkspace.objects.create(
        product=product,
        reference=f"{program_key}-{product.pk}",
        experience_type=program_key,
    )
    return product


def support_grant(user, category, *, program="abdm", action="read"):
    return AccessGrant.objects.create(
        user=user,
        program=program,
        area="support",
        category=category,
        can_read=True,
        can_write=action == "write",
        can_approve=action == "approve",
    )


def visible(user, action="read"):
    return {
        ticket.category for ticket in permissions.visible_tickets(user, action=action)
    }


@pytest.fixture
def staff():
    return UserFactory(is_nha_team=True)


@pytest.fixture
def tickets(owner_membership):
    """One ticket per category an ABDM ticket can carry, general included."""
    product = product_in("abdm", owner_membership)
    return {
        category: Ticket.objects.create(
            organisation=owner_membership.organisation,
            product=product,
            category=category,
            subject=f"{category or 'General'} request",
            created_by=owner_membership.user,
        )
        for category in ["", *TRACKS, RETIRED]
    }


@pytest.fixture
def supplier_program(monkeypatch):
    """A second registered program, with the portal still defaulting to ABDM."""
    for attribute in ("_definitions", "_forms", "_programs"):
        monkeypatch.setattr(registry, attribute, dict(getattr(registry, attribute)))
    registry.register_program(SupplierQuality)
    return SupplierQuality


@pytest.fixture
def supplier_ticket(supplier_program, owner_membership):
    return Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=product_in(supplier_program.key, owner_membership, name="Water pump"),
        category="Quality",
        subject="Inspection sandbox is down",
        created_by=owner_membership.user,
    )


@pytest.mark.parametrize("category", ["", *TRACKS])
def test_a_grant_sees_its_own_category_and_nothing_else(tickets, staff, category):
    support_grant(staff, category)

    assert visible(staff) == {category}


def test_the_wildcard_sees_every_category_a_ticket_carries(tickets, staff):
    support_grant(staff, "*")

    assert visible(staff) == {"", *TRACKS, RETIRED}


def test_a_retired_category_is_reachable_only_through_the_wildcard(tickets, staff):
    for category in ["", *TRACKS]:
        support_grant(staff, category)

    assert RETIRED not in visible(staff)


def test_separate_grants_add_up(tickets, staff):
    support_grant(staff, "UHI")
    support_grant(staff, "NHCX")

    assert visible(staff) == {"UHI", "NHCX"}


def test_review_access_does_not_carry_into_support(tickets, staff):
    AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="review",
        category="*",
        can_read=True,
        can_write=True,
        can_approve=True,
    )

    assert not permissions.visible_tickets(staff).exists()
    assert not permissions.has_area(staff, "support")


@pytest.mark.parametrize(
    ("action", "can_reply", "can_close"),
    [("read", False, False), ("write", True, False), ("approve", False, True)],
)
def test_reply_and_close_follow_the_action_on_the_grant(
    tickets,
    staff,
    action,
    can_reply,
    can_close,
):
    support_grant(staff, "UHI", action=action)
    ticket = tickets["UHI"]

    assert visible(staff) == {"UHI"}
    assert permissions.can_reply_ticket(staff, ticket) is can_reply
    assert permissions.can_close_ticket(staff, ticket) is can_close
    assert visible(staff, "write") == ({"UHI"} if can_reply else set())
    assert visible(staff, "approve") == ({"UHI"} if can_close else set())


def test_an_action_stops_at_the_category_it_was_granted_for(tickets, staff):
    support_grant(staff, "UHI", action="write")
    support_grant(staff, "NHCX")

    assert permissions.can_reply_ticket(staff, tickets["UHI"])
    assert not permissions.can_reply_ticket(staff, tickets["NHCX"])
    assert not permissions.can_close_ticket(staff, tickets["UHI"])


def test_a_category_does_not_cross_into_another_program(
    tickets,
    supplier_ticket,
    staff,
):
    support_grant(staff, "*")

    assert supplier_ticket not in permissions.visible_tickets(staff)

    support_grant(staff, "Quality", program=SupplierQuality.key, action="write")

    assert supplier_ticket in permissions.visible_tickets(staff)
    assert permissions.can_reply_ticket(staff, supplier_ticket)
    assert not permissions.can_reply_ticket(staff, tickets["UHI"])


def test_support_access_in_another_program_still_opens_support(
    supplier_ticket,
    staff,
    client,
):
    """The support screens list every program, so the gate cannot ask for one."""
    support_grant(staff, "*", program=SupplierQuality.key)
    client.force_login(staff)

    assert list(permissions.visible_tickets(staff)) == [supplier_ticket]
    assert permissions.has_area(staff, "support")
    assert permissions.staff_home(staff) == "experiences:support"

    page = client.get(reverse("experiences:support"))

    assert page.status_code == HTTPStatus.OK
    assert list(page.context["tickets"]) == [supplier_ticket]


def test_a_grant_for_a_program_the_portal_no_longer_runs_opens_nothing(tickets, staff):
    support_grant(staff, "*", program=SupplierQuality.key)

    assert not permissions.has_area(staff, "support")
    assert not permissions.visible_tickets(staff).exists()


def test_an_integrator_sees_their_own_tickets_whatever_the_category(
    tickets,
    owner_membership,
):
    user = owner_membership.user

    assert visible(user) == {"", *TRACKS, RETIRED}
    assert permissions.can_reply_ticket(user, tickets["UHI"])
    assert permissions.can_close_ticket(user, tickets["UHI"])
    # Categories scope staff, so an integrator has no write-scoped queryset;
    # their own membership is what lets them answer.
    assert not permissions.visible_tickets(user, "write").exists()


def test_outsiders_and_signed_out_visitors_see_nothing(tickets):
    outsider = MembershipFactory.create(
        organisation=OrganisationFactory.create(name="Other Health"),
        role="owner",
    ).user

    assert not permissions.visible_tickets(outsider).exists()
    assert not permissions.can_reply_ticket(outsider, tickets["UHI"])
    assert not permissions.visible_tickets(AnonymousUser()).exists()


def test_a_deactivated_reviewers_grants_stop_counting(tickets, staff):
    support_grant(staff, "*", action="write")
    staff.is_active = False
    staff.save(update_fields=["is_active"])

    assert not permissions.visible_tickets(staff).exists()
    assert not permissions.can_reply_ticket(staff, tickets["UHI"])


def test_a_superuser_needs_no_grant_in_any_program(tickets, supplier_ticket):
    admin = UserFactory(is_superuser=True, is_nha_team=True)

    assert set(permissions.visible_tickets(admin, "approve")) == {
        *tickets.values(),
        supplier_ticket,
    }


def test_every_category_a_ticket_can_be_filed_under_can_be_granted():
    program = get_program()
    filed = {
        value
        for value, _label in SupportForm(program=program).fields["category"].choices
    }

    assert filed <= {"", "*", *program.track_map()}


def test_a_grant_cannot_name_a_category_no_track_uses(staff):
    grant = AccessGrant(
        user=staff,
        program="abdm",
        area="support",
        category=RETIRED,
        can_read=True,
    )

    with pytest.raises(ValidationError):
        grant.clean()
