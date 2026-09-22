# ruff: noqa: F811
from http import HTTPStatus

import pytest
from django.urls import reverse

from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.integrations.models import ProvisioningRun
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
        f"#review-{approved.pk}",
        f"#review-{pending.pk}",
    }
    assert track_url(environment, "HIE-CM").encode() not in response.content


def test_category_reviewers_see_their_tracks_and_act_only_with_a_grant(
    environment,
    client,
):
    approve(environment)
    locker = submit(environment, "p4")
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
        category="HIE-CM",
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


def test_only_a_super_admin_is_offered_the_revoke_button(environment, client):
    approve(environment)
    reviewer = staff("*", can_write=True)

    client.force_login(reviewer)
    assert b"revoke_credentials" not in client.get(product_url(environment)).content

    client.force_login(environment["admin"])
    response = client.get(product_url(environment))

    assert response.context["can_revoke_credentials"]
    assert b"revoke_credentials" in response.content


def test_a_super_admin_revokes_the_credentials_from_the_product(
    environment,
    client,
    django_capture_on_commit_callbacks,
):
    approve(environment)
    credential = ProductCredential.objects.get(product=environment["workspace"].product)
    client.force_login(environment["admin"])

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            product_url(environment),
            {"intent": "revoke_credentials"},
        )

    assert response.status_code == HTTPStatus.FOUND
    assert response.url == f"{product_url(environment)}#connection"
    credential.refresh_from_db()
    assert credential.status == "revoked"
    # Revoked once, the action is spent: the button goes with it.
    assert b"revoke_credentials" not in client.get(product_url(environment)).content


def test_a_reviewer_cannot_revoke_by_posting_the_intent(environment, client):
    approve(environment)
    client.force_login(staff("*", can_write=True, can_approve=True))

    response = client.post(product_url(environment), {"intent": "revoke_credentials"})

    assert response.status_code == HTTPStatus.FORBIDDEN
    credential = ProductCredential.objects.get(product=environment["workspace"].product)
    assert credential.status == "active"


def test_a_revoked_integrator_is_sent_to_support_not_offered_new_credentials(
    environment,
    client,
    django_capture_on_commit_callbacks,
):
    workspace = environment["workspace"]
    client.force_login(environment["admin"])
    with django_capture_on_commit_callbacks(execute=True):
        client.post(product_url(environment), {"intent": "revoke_credentials"})
    url = reverse("experiences:credentials", args=[workspace.reference])
    client.force_login(environment["applicant"])

    content = client.get(url).content.decode()

    assert "Your credentials have been revoked." in content
    assert f"{reverse('experiences:support')}?product={workspace.reference}" in content
    assert 'value="rotate"' not in content
    assert workspace.definition.sandbox_credentials.demo_notice not in content
    client.post(url, {"intent": "rotate"})
    credential = ProductCredential.objects.get(product=workspace.product)
    assert credential.status == "revoked"


def _revoked(environment, client, capture):
    """Revoked by a super admin, after the READY run a registration leaves."""
    ProvisioningRun.objects.create(
        product=environment["workspace"].product,
        status=ProvisioningRun.Status.READY,
    )
    client.force_login(environment["admin"])
    with capture(execute=True):
        client.post(product_url(environment), {"intent": "revoke_credentials"})


def test_only_a_super_admin_is_offered_reprovisioning(
    environment,
    client,
    django_capture_on_commit_callbacks,
):
    approve(environment)
    _revoked(environment, client, django_capture_on_commit_callbacks)

    response = client.get(product_url(environment))
    assert response.context["can_reprovision"]
    assert b"reprovision_credentials" in response.content

    client.force_login(staff("*", can_write=True, can_approve=True))
    content = client.get(product_url(environment)).content
    assert b"reprovision_credentials" not in content
    response = client.post(
        product_url(environment),
        {"intent": "reprovision_credentials"},
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


def test_a_super_admin_reprovisions_revoked_credentials(
    environment,
    client,
    django_capture_on_commit_callbacks,
):
    approve(environment)
    workspace = environment["workspace"]
    client_id = ProductCredential.objects.get(product=workspace.product).client_id
    _revoked(environment, client, django_capture_on_commit_callbacks)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            product_url(environment),
            {"intent": "reprovision_credentials"},
        )

    assert response.url == f"{product_url(environment)}#connection"
    credential = ProductCredential.objects.get(product=workspace.product)
    assert credential.status == "active"
    assert credential.client_id == client_id
    content = client.get(product_url(environment)).content
    assert b"reprovision_credentials" not in content
    assert b"revoke_credentials" in content
    client.force_login(environment["applicant"])
    url = reverse("experiences:credentials", args=[workspace.reference])
    assert b'value="rotate"' in client.get(url).content
