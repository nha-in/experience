"""The catch-all support category taking a code of its own, "others".

Its tickets and the permissions for them have to move together, or the tickets
drop out of the queue of the people who answer them. Blank still means general
and onboarding work to review and events, so their grants stay as they are.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductWorkspace
from ohc_experience.support.models import Ticket
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BEFORE = [
    ("experiences", "0031_split_phr_into_phases"),
    ("support", "0011_ticketmessage_priority_change"),
]
AFTER = [
    ("experiences", "0032_others_support_grant_code"),
    ("support", "0012_others_category_code"),
]


def latest():
    """The tip, so the next test does not start a migration behind it."""
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


@pytest.fixture
def at_before():
    MigrationExecutor(connection).migrate(BEFORE)
    yield MigrationExecutor(connection).loader.project_state(BEFORE).apps
    MigrationExecutor(connection).migrate(latest())


def product_in(program, membership):
    """A product registered in a program, which is what ties a ticket to it."""
    product = Product.objects.create(
        organisation=membership.organisation,
        name=f"{program} product",
        description="A product under test.",
        created_by=membership.user,
    )
    ProductWorkspace.objects.create(
        product=product,
        reference=f"{program}-{product.pk}",
        experience_type=program,
    )
    return product


def file_ticket(apps, number, product, category):
    return (
        apps.get_model("support", "Ticket")
        .objects.create(
            reference=f"TKT-{number}",
            organisation_id=product.organisation_id,
            product_id=product.pk,
            subject="Help",
            category=category,
        )
        .pk
    )


def grant(apps, user, area, category):
    apps.get_model("experiences", "AccessGrant").objects.create(
        user_id=user.pk,
        program="abdm",
        area=area,
        category=category,
        can_read=True,
    )


def grants(user):
    return set(AccessGrant.objects.filter(user=user).values_list("area", "category"))


def test_the_catch_all_tickets_take_its_code(at_before, owner_membership):
    abdm = product_in("abdm", owner_membership)
    others = file_ticket(at_before, 1, abdm, "")
    milestone = file_ticket(at_before, 2, abdm, "abdm-m1")
    # Others is ABDM's catch-all; a blank another program left means its own.
    unrelated = file_ticket(
        at_before,
        3,
        product_in("supplier-quality", owner_membership),
        "",
    )

    MigrationExecutor(connection).migrate(AFTER)

    assert dict(Ticket.objects.values_list("pk", "category")) == {
        others: "others",
        milestone: "abdm-m1",
        unrelated: "",
    }


def test_only_support_grants_for_the_catch_all_move(at_before):
    user = UserFactory(is_nha_team=True)
    for area, category in (
        ("support", ""),
        ("support", "*"),
        ("review", ""),
        ("events", ""),
    ):
        grant(at_before, user, area, category)

    MigrationExecutor(connection).migrate(AFTER)

    assert grants(user) == {
        ("support", "others"),
        ("support", "*"),
        ("review", ""),
        ("events", ""),
    }


def test_the_way_back_returns_the_catch_all_to_blank(at_before, owner_membership):
    user = UserFactory(is_nha_team=True)
    grant(at_before, user, "support", "")
    ticket = file_ticket(at_before, 1, product_in("abdm", owner_membership), "")
    MigrationExecutor(connection).migrate(AFTER)

    MigrationExecutor(connection).migrate(BEFORE)

    assert grants(user) == {("support", "")}
    assert Ticket.objects.get(pk=ticket).category == ""
