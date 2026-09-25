"""Support permissions, read through the tickets they are there to scope.

Staff support access is granted per program and per support category: a code
from the program's support menu, the catch-all's "others" among them, or "*"
for the lot. A ticket's category is the other half of that pair, so these tests
keep real grants and real tickets together rather than asserting on the query
that joins them. Review and events are still granted by track, which is why a track code
here reads as a category support has retired.
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

CATEGORIES = [
    "abdm-m1",
    "abdm-m2",
    "abdm-m3",
    "abdm-m4",
    "abdm-review",
    "abdm-scan-share",
    "phr-app-p1",
    "nhcx-auth",
    "nhcx-workflow",
    "nhcx-data",
    "uhi-service",
    "others",
]
#: A category the support menu does not answer to, as the tracks support used to
#: be filed by leave behind on the tickets that were filed under them.
RETIRED = "ABDM"


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
    """One ticket per category an ABDM ticket can carry, Others included."""
    product = product_in("abdm", owner_membership)
    return {
        category: Ticket.objects.create(
            organisation=owner_membership.organisation,
            product=product,
            category=category,
            subject=f"{category} request",
            created_by=owner_membership.user,
        )
        for category in [*CATEGORIES, RETIRED]
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


@pytest.mark.parametrize("category", CATEGORIES)
def test_a_grant_sees_its_own_category_and_nothing_else(tickets, staff, category):
    support_grant(staff, category)

    assert visible(staff) == {category}


def test_the_wildcard_sees_every_category_a_ticket_carries(tickets, staff):
    support_grant(staff, "*")

    assert visible(staff) == {*CATEGORIES, RETIRED}


def test_a_retired_category_is_reachable_only_through_the_wildcard(tickets, staff):
    for category in CATEGORIES:
        support_grant(staff, category)

    assert RETIRED not in visible(staff)


def test_separate_grants_add_up(tickets, staff):
    support_grant(staff, "abdm-m1")
    support_grant(staff, "nhcx-data")

    assert visible(staff) == {"abdm-m1", "nhcx-data"}


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
def test_reply_priority_and_close_follow_the_action_on_the_grant(
    tickets,
    staff,
    action,
    can_reply,
    can_close,
):
    support_grant(staff, "abdm-m1", action=action)
    ticket = tickets["abdm-m1"]

    assert visible(staff) == {"abdm-m1"}
    assert permissions.can_reply_ticket(staff, ticket) is can_reply
    # Whoever may reply may also correct the priority.
    assert permissions.can_change_ticket_priority(staff, ticket) is can_reply
    assert permissions.can_close_ticket(staff, ticket) is can_close
    assert visible(staff, "write") == ({"abdm-m1"} if can_reply else set())
    assert visible(staff, "approve") == ({"abdm-m1"} if can_close else set())


def test_an_action_stops_at_the_category_it_was_granted_for(tickets, staff):
    support_grant(staff, "abdm-m1", action="write")
    support_grant(staff, "nhcx-data")

    assert permissions.can_reply_ticket(staff, tickets["abdm-m1"])
    assert not permissions.can_reply_ticket(staff, tickets["nhcx-data"])
    assert not permissions.can_change_ticket_priority(staff, tickets["nhcx-data"])
    assert not permissions.can_close_ticket(staff, tickets["abdm-m1"])


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
    assert not permissions.can_reply_ticket(staff, tickets["abdm-m1"])


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

    assert visible(user) == {*CATEGORIES, RETIRED}
    assert permissions.can_reply_ticket(user, tickets["abdm-m1"])
    assert permissions.can_close_ticket(user, tickets["abdm-m1"])
    # They choose a priority when filing, and leave any change to the NHA team.
    assert not permissions.can_change_ticket_priority(user, tickets["abdm-m1"])
    # Categories scope staff, so an integrator has no write-scoped queryset;
    # their own membership is what lets them answer.
    assert not permissions.visible_tickets(user, "write").exists()


def test_outsiders_and_signed_out_visitors_see_nothing(tickets):
    outsider = MembershipFactory.create(
        organisation=OrganisationFactory.create(name="Other Health"),
        role="owner",
    ).user

    assert not permissions.visible_tickets(outsider).exists()
    assert not permissions.can_reply_ticket(outsider, tickets["abdm-m1"])
    assert not permissions.visible_tickets(AnonymousUser()).exists()


def test_a_deactivated_reviewers_grants_stop_counting(tickets, staff):
    support_grant(staff, "*", action="write")
    staff.is_active = False
    staff.save(update_fields=["is_active"])

    assert not permissions.visible_tickets(staff).exists()
    assert not permissions.can_reply_ticket(staff, tickets["abdm-m1"])


def test_a_superuser_needs_no_grant_in_any_program(tickets, supplier_ticket):
    admin = UserFactory(is_superuser=True, is_nha_team=True)

    assert set(permissions.visible_tickets(admin, "approve")) == {
        *tickets.values(),
        supplier_ticket,
    }
    assert permissions.can_change_ticket_priority(admin, supplier_ticket)


def test_every_category_a_ticket_can_be_filed_under_can_be_granted():
    program = get_program()
    placeholder, *filed = SupportForm(program=program).fields["category"].choices

    # The blank choice only asks for one, and a ticket cannot be filed under it.
    assert placeholder == ("", "Select a category")
    assert {value for value, _label in filed} <= set(program.support_category_map())


def test_the_sub_menu_carries_no_permission_of_its_own(tickets, staff):
    """Two tickets in one category answer to the same grant, issue type aside."""
    support_grant(staff, "abdm-m2")
    for issue_type in ("Bridge Service", "Data Transfer"):
        Ticket.objects.filter(pk=tickets["abdm-m2"].pk).update(issue_type=issue_type)

        assert (
            permissions.visible_tickets(staff)
            .filter(
                pk=tickets["abdm-m2"].pk,
            )
            .exists()
        )


def test_a_grant_cannot_name_a_category_the_support_menu_dropped(staff):
    grant = AccessGrant(
        user=staff,
        program="abdm",
        area="support",
        category=RETIRED,
        can_read=True,
    )

    with pytest.raises(ValidationError):
        grant.clean()


def test_each_area_is_granted_in_its_own_vocabulary(staff):
    """RETIRED is a live review track, and a support category no longer exists."""

    def grant(area, category):
        return AccessGrant(
            user=staff,
            program="abdm",
            area=area,
            category=category,
            can_read=True,
        )

    assert grant("review", RETIRED).clean() is None
    assert grant("support", "abdm-m1").clean() is None
    with pytest.raises(ValidationError):
        grant("review", "abdm-m1").clean()
    with pytest.raises(ValidationError):
        grant("support", RETIRED).clean()
    # Blank is general and onboarding work to review; support's catch-all has a
    # code of its own, so a blank support grant would match no ticket.
    assert grant("review", "").clean() is None
    assert grant("support", "others").clean() is None
    with pytest.raises(ValidationError):
        grant("support", "").clean()
