# ruff: noqa: F811
from http import HTTPStatus

import pytest
from django.urls import reverse

from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import phr_product
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import production
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.integrations.local import fail_next
from ohc_experience.integrations.models import ProvisioningRun
from ohc_experience.integrations.ports import UNSUPPORTED
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.support.models import Ticket
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def product_url(environment, product=None):
    product = product or environment["product"]
    return reverse("experiences:product-detail", args=[product.reference])


def track_url(environment, code, product=None):
    product = product or environment["product"]
    return reverse("experiences:track", args=[product.reference, code])


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
    assert f'id="review-{pending.pk}"'.encode() in response.content
    assert b'id="product-switcher' not in response.content
    assert response.context["registration"].kind == "product_registration"
    tiles = [tile for row in response.context["tracks"] for tile in row["tiles"]]
    assert {tile["url"] for tile in tiles} >= {
        f"#review-{approved.pk}",
        f"#review-{pending.pk}",
    }
    assert track_url(environment, "ABDM").encode() not in response.content


def test_category_reviewers_see_their_tracks_and_act_only_with_a_grant(
    environment,
    client,
):
    approve(environment)
    locker = submit(environment, "p1")
    hidden = submit(environment, "m2")
    reader = staff("PHR")
    client.force_login(reader)

    response = client.get(product_url(environment, phr_product(environment)))

    assert response.status_code == HTTPStatus.OK
    assert [row["definition"].code for row in response.context["tracks"]] == [
        "PHR",
    ]
    assert response.context["pending"] == [locker]
    assert response.context["decidable"] == set()
    assert hidden.get_absolute_url().encode() not in response.content
    assert response.context["registration"] is None
    assert response.context["credential"] is None
    assert not response.context["certification"]
    assert b"Integration connection" not in response.content

    client.force_login(staff("PHR", can_approve=True))
    response = client.get(product_url(environment, phr_product(environment)))
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

    assert client.get(environment["product"].get_absolute_url()).url == (
        product_url(environment)
    )
    response = client.get(track_url(environment, "ABDM"), {"milestone": "m1"})
    assert response.url == m1.get_absolute_url()
    assert client.get(track_url(environment, "ABDM")).url == (
        f"{product_url(environment)}#track-abdm"
    )
    assert client.get(track_url(environment, "Unknown")).status_code == (
        HTTPStatus.NOT_FOUND
    )

    # M1 belongs to ABDM, so a UHI reviewer lands on the track, not on M1.
    client.force_login(staff("UHI"))
    response = client.get(track_url(environment, "UHI"), {"milestone": "m1"})
    assert response.url == f"{product_url(environment)}#track-uhi"
    client.force_login(staff("PHR"))
    response = client.get(
        track_url(environment, "PHR", phr_product(environment)),
        {"milestone": "m1"},
    )
    assert response.url == (
        f"{product_url(environment, phr_product(environment))}#track-phr"
    )
    assert client.get(track_url(environment, "ABDM")).status_code == (
        HTTPStatus.NOT_FOUND
    )

    client.force_login(environment["applicant"])
    assert client.get(track_url(environment, "ABDM")).status_code == HTTPStatus.OK


def test_staff_pages_never_offer_the_product_switcher(environment, client):
    item = approve(environment)
    ticket = Ticket.objects.create(
        organisation=environment["org"],
        product=environment["product"],
        created_by=environment["applicant"],
        subject="Callback help",
        category="ABDM",
    )
    staff_pages = [
        reverse("experiences:ticket", args=[ticket.reference]),
        reverse("experiences:submission", args=[item.pk, item.selected_submission_id]),
        reverse("experiences:products"),
        product_url(environment),
    ]
    client.force_login(environment["admin"])
    for url in staff_pages:
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK, url
        assert b'id="product-switcher' not in response.content, url
    assert (
        product_url(environment).encode()
        in client.get(reverse("experiences:products")).content
    )

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


def connection_card(html):
    start = html.index('id="connection"')
    return html[start : html.index("</section>", start)]


def test_the_connection_card_shows_each_value_whole_with_a_copy_button(
    environment,
    client,
):
    """Long values wrap instead of being cut off, and each one copies."""
    approve(environment)
    production.record(
        environment["product"],
        environment["admin"],
        client_id="DEMO-PROD-MEDIBASE-HMIS-0001",
        expected="",
    )
    credential = ProductCredential.objects.get(product=environment["product"])
    client.force_login(environment["admin"])

    card = connection_card(client.get(product_url(environment)).content.decode())

    assert "truncate" not in card
    for value, label in (
        (credential.client_id, "client ID"),
        (credential.callback_url, "callback URL"),
        ("DEMO-PROD-MEDIBASE-HMIS-0001", "production client ID"),
    ):
        assert f'<span class="min-w-0 break-all font-mono">{value}</span>' in card
        assert f'data-copy="{value}"' in card
        assert f'aria-label="Copy {label}"' in card

    ProductCredential.objects.filter(pk=credential.pk).update(callback_url="")
    card = connection_card(client.get(product_url(environment)).content.decode())

    assert "Not configured" in card
    assert "Copy callback URL" not in card
    assert "Copy client ID" in card


def test_a_super_admin_revokes_the_credentials_from_the_product(
    environment,
    client,
    django_capture_on_commit_callbacks,
):
    approve(environment)
    credential = ProductCredential.objects.get(product=environment["product"])
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
    credential = ProductCredential.objects.get(product=environment["product"])
    assert credential.status == "active"


def test_a_revoked_integrator_is_sent_to_support_not_offered_new_credentials(
    environment,
    client,
    django_capture_on_commit_callbacks,
):
    product = environment["product"]
    client.force_login(environment["admin"])
    with django_capture_on_commit_callbacks(execute=True):
        client.post(product_url(environment), {"intent": "revoke_credentials"})
    url = reverse("experiences:credentials", args=[product.reference])
    client.force_login(environment["applicant"])

    content = client.get(url).content.decode()

    assert "Your credentials have been revoked." in content
    assert f"{reverse('experiences:support')}?product={product.reference}" in content
    assert 'value="reveal"' not in content
    assert product.definition.sandbox_credentials.demo_notice not in content
    client.post(url, {"intent": "rotate"})
    credential = ProductCredential.objects.get(product=product)
    assert credential.status == "revoked"


def _revoked(environment, client, capture):
    """Revoked by a super admin, after the READY run a registration leaves."""
    ProvisioningRun.objects.create(
        product=environment["product"],
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
    product = environment["product"]
    client_id = ProductCredential.objects.get(product=product).client_id
    _revoked(environment, client, django_capture_on_commit_callbacks)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            product_url(environment),
            {"intent": "reprovision_credentials"},
        )

    assert response.url == f"{product_url(environment)}#connection"
    credential = ProductCredential.objects.get(product=product)
    assert credential.status == "active"
    assert credential.client_id == client_id
    content = client.get(product_url(environment)).content
    assert b"reprovision_credentials" not in content
    assert b"revoke_credentials" in content
    client.force_login(environment["applicant"])
    url = reverse("experiences:credentials", args=[product.reference])
    assert b'value="reveal"' in client.get(url).content


def test_a_super_admin_retries_a_teardown_that_stopped_short(
    environment,
    client,
    django_capture_on_commit_callbacks,
):
    approve(environment)
    fail_next(ExternalSystem.KEYCLOAK, "disable_client", retryable=False)
    _revoked(environment, client, django_capture_on_commit_callbacks)

    response = client.get(product_url(environment))
    assert response.context["can_retry_deprovisioning"]
    assert not response.context["can_reprovision"]
    client.force_login(staff("*", can_write=True, can_approve=True))
    assert b"retry_deprovisioning" not in client.get(product_url(environment)).content

    client.force_login(environment["admin"])
    with django_capture_on_commit_callbacks(execute=True):
        client.post(product_url(environment), {"intent": "retry_deprovisioning"})

    response = client.get(product_url(environment))
    assert not response.context["can_retry_deprovisioning"]
    assert response.context["can_reprovision"]


def test_a_gateway_left_subscribed_reads_as_disabled_and_allows_reprovisioning(
    environment,
    client,
    django_capture_on_commit_callbacks,
):
    approve(environment)
    fail_next(ExternalSystem.WSO2, "unsubscribe", code=UNSUPPORTED, retryable=False)
    _revoked(environment, client, django_capture_on_commit_callbacks)

    response = client.get(product_url(environment))

    rows = response.context["provisioning"]
    gateway = next(row for row in rows if row.system == "WSO2")
    assert (gateway.state, str(gateway.display)) == ("LEFT_SUBSCRIBED", "Disabled")
    assert b"Left subscribed" not in response.content
    assert not response.context["can_retry_deprovisioning"]
    assert response.context["can_reprovision"]
