# ruff: noqa: PLR2004
import pytest
from django.test import Client
from django.urls import reverse

from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.experiences import workflows
from ohc_experience.experiences.context_processors import navigation_context
from ohc_experience.experiences.models import ProductWorkspace
from ohc_experience.integrations.services import provision_inline
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_selected_product_follows_integrator_into_account_pages(environment):  # noqa: F811
    user = environment["applicant"]
    data = {
        **product_data(),
        "name": "Second product",
        "applied_milestones": ["HealthLocker:p4"],
    }
    second, form = workflows.register_product(environment["org"], user, data=data)
    assert second, form.errors
    client = Client()
    client.force_login(user)
    response = client.get(second.get_absolute_url())
    assert response.status_code == 200
    for url in (reverse("users:profile"), reverse("organisations:team")):
        response = client.get(url)
        assert response.status_code == 200
        assert response.context["workspace"].pk == second.pk
        assert second.product.name.encode() in response.content
        nav = (
            response.content.decode()
            .split('<nav id="app-nav"', 1)[1]
            .split("</nav>", 1)[0]
        )
        assert "nav-track-healthlocker" in nav
        assert "nav-track-hie-cm" not in nav


def test_switcher_never_lists_another_organisations_products(environment):  # noqa: F811
    membership = MembershipFactory(user=UserFactory(), organisation__onboarded=True)
    other, form = workflows.register_product(
        membership.organisation,
        membership.user,
        data={**product_data(), "name": "Private other product"},
    )
    assert other, form.errors
    client = Client()
    client.force_login(environment["applicant"])
    session = client.session
    session["experience_product"] = other.reference
    session.save()
    response = client.get(reverse("users:profile"))
    assert response.context["workspace"].pk == environment["workspace"].pk
    assert b"Private other product" not in response.content
    assert client.get(other.get_absolute_url()).status_code == 404


def test_boosted_navigation_returns_the_main_and_updated_rail(environment):  # noqa: F811
    client = Client()
    client.force_login(environment["applicant"])
    response = client.get(
        environment["workspace"].get_absolute_url(),
        HTTP_HX_REQUEST="true",
        HTTP_HX_BOOSTED="true",
    )
    assert response.status_code == 200
    assert response.content.count(b'id="main-content"') == 1
    assert response.content.count(b'id="app-nav"') == 1
    assert b'id="product-switcher"' in response.content
    assert b'id="product-switcher-card"' in response.content
    assert b'hx-history="false"' in response.content


def test_applied_tracks_only_and_approval_counts(environment, rf):  # noqa: F811
    workspace = environment["workspace"]
    ProductWorkspace.objects.filter(pk=workspace.pk).update(
        applied_milestones=["HIE-CM:m1"],
    )
    workspace.refresh_from_db()
    request = rf.get("/")
    request.user = environment["applicant"]
    context = navigation_context(request, workspace)
    assert [
        (row["definition"].code, row["count"]) for row in context["nav_tracks"]
    ] == [("HIE-CM", 1)]
    assert context["nav_tracks"][0]["approved"] == 0


def test_a_track_counts_only_the_milestones_the_product_applied_for(environment, rf):  # noqa: F811
    """UHI shows M2 as related context, but a product without it counts 2, not 3."""
    workspace, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data={
            **product_data("M1 and UHI only"),
            "applied_milestones": ["HIE-CM:m1", "UHI:uhi1"],
        },
    )
    assert workspace, form.errors
    provision_inline(workspace.product)
    workspace.refresh_from_db()
    assert not workspace.product.milestones.filter(key="m2").exists()

    request = rf.get("/")
    request.user = environment["applicant"]
    counts = {
        row["definition"].code: (row["approved"], row["count"])
        for row in navigation_context(request, workspace)["nav_tracks"]
    }

    assert counts == {"HIE-CM": (0, 1), "UHI": (0, 2)}


def test_sidebar_offers_only_setup_links_before_a_product_exists(
    client,
    owner_membership,
):
    client.force_login(owner_membership.user)
    html = client.get(reverse("experiences:product-create")).content.decode()
    main_nav = html[html.index('id="app-nav"') : html.index('class="mt-auto')]

    for link in ("dashboard", "products", "register", "organisation"):
        assert f'id="nav-{link}"' in main_nav
    for link in ("queries", "events", "support"):
        assert f'id="nav-{link}"' not in main_nav
    assert "Programme" not in main_nav
