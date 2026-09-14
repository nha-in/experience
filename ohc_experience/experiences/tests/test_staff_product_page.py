# ruff: noqa: F811
from http import HTTPStatus

import pytest
from django.urls import reverse

from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.support.models import Ticket
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def product_url(environment):
    return reverse(
        "experiences:product-detail",
        args=[environment["workspace"].reference],
    )


def track_url(environment, code):
    return reverse(
        "experiences:track",
        args=[environment["workspace"].reference, code],
    )


def staff(category, **actions):
    user = UserFactory(is_nha_team=True, is_staff=True)
    AccessGrant.objects.create(
        user=user,
        program="abdm",
        area="review",
        category=category,
        can_read=True,
        **actions,
    )
    return user


def test_admin_reaches_each_open_request_from_the_product(environment, client):
    approved = approve(environment)
    pending = submit(environment, "m2")
    client.force_login(environment["admin"])

    response = client.get(product_url(environment))

    assert response.status_code == HTTPStatus.OK
    assert response.context["pending"] == [pending]
    assert response.context["decidable"] == {pending.pk}
    assert pending.get_absolute_url().encode() in response.content
    assert b'id="product-switcher' not in response.content
    assert response.context["registration"].kind == "product_registration"
    tiles = [tile for row in response.context["tracks"] for tile in row["tiles"]]
    assert {tile["url"] for tile in tiles} >= {
        approved.get_absolute_url(),
        pending.get_absolute_url(),
    }
    assert track_url(environment, "HIE-CM").encode() not in response.content


def test_category_reviewers_see_their_tracks_and_act_only_with_a_grant(
    environment,
    client,
):
    approve(environment)
    locker = submit(environment, "locker1")
    hidden = submit(environment, "m2")
    reader = staff("HealthLocker")
    client.force_login(reader)

    response = client.get(product_url(environment))

    assert response.status_code == HTTPStatus.OK
    assert [row["definition"].code for row in response.context["tracks"]] == [
        "HealthLocker",
    ]
    assert response.context["pending"] == [locker]
    assert response.context["decidable"] == set()
    assert hidden.get_absolute_url().encode() not in response.content
    assert response.context["registration"] is None
    assert response.context["credential"] is None
    assert not response.context["certification"]
    assert b"Integration connection" not in response.content

    client.force_login(staff("HealthLocker", can_approve=True))
    response = client.get(product_url(environment))
    assert response.context["decidable"] == {locker.pk}


def test_staff_product_page_is_scoped_to_visible_reviews(environment, client):
    client.force_login(staff("NHCX", can_write=True, can_approve=True))
    assert client.get(product_url(environment)).status_code == HTTPStatus.NOT_FOUND
    for key in ("applicant", "outsider"):
        client.force_login(environment[key])
        assert client.get(product_url(environment)).status_code == (
            HTTPStatus.FORBIDDEN
        )


def test_staff_links_into_integrator_pages_land_on_staff_pages(environment, client):
    m1 = milestone(environment, "m1")
    client.force_login(environment["admin"])

    assert client.get(environment["workspace"].get_absolute_url()).url == (
        product_url(environment)
    )
    response = client.get(track_url(environment, "HIE-CM"), {"milestone": "m1"})
    assert response.url == m1.get_absolute_url()
    assert client.get(track_url(environment, "HIE-CM")).url == (
        f"{product_url(environment)}#track-hie-cm"
    )
    assert client.get(track_url(environment, "Unknown")).status_code == (
        HTTPStatus.NOT_FOUND
    )

    client.force_login(staff("UHI"))
    response = client.get(track_url(environment, "UHI"), {"milestone": "m1"})
    assert response.url == m1.get_absolute_url()
    client.force_login(staff("HealthLocker"))
    response = client.get(track_url(environment, "HealthLocker"), {"milestone": "m1"})
    assert response.url == f"{product_url(environment)}#track-healthlocker"
    assert client.get(track_url(environment, "HIE-CM")).status_code == (
        HTTPStatus.NOT_FOUND
    )

    client.force_login(environment["applicant"])
    assert client.get(track_url(environment, "HIE-CM")).status_code == HTTPStatus.OK


def test_staff_pages_never_offer_the_product_switcher(environment, client):
    item = approve(environment)
    ticket = Ticket.objects.create(
        organisation=environment["org"],
        product=environment["workspace"].product,
        created_by=environment["applicant"],
        subject="Callback help",
        category="api",
    )
    staff_pages = [
        reverse("experiences:ticket", args=[ticket.reference]),
        reverse("experiences:submission", args=[item.pk, item.selected_submission_id]),
        reverse("experiences:products"),
        item.get_absolute_url(),
    ]
    client.force_login(environment["admin"])
    for url in staff_pages:
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK, url
        assert b'id="product-switcher' not in response.content, url
    for url in (reverse("experiences:products"), item.get_absolute_url()):
        assert product_url(environment).encode() in client.get(url).content, url

    client.force_login(environment["applicant"])
    response = client.get(reverse("experiences:ticket", args=[ticket.reference]))
    assert b'id="product-switcher-card"' in response.content
    response = client.get(reverse("experiences:products"))
    assert product_url(environment).encode() not in response.content
