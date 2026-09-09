# ruff: noqa: PLR2004
import pytest
from django.test import Client
from django.urls import reverse

from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.experiences import workflows
from ohc_experience.experiences.context_processors import navigation_context
from ohc_experience.experiences.models import ProductWorkspace
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_selected_product_follows_integrator_into_account_pages(environment):  # noqa: F811
    user = environment["applicant"]
    data = {
        **product_data(),
        "name": "Second product",
        "applied_milestones": ["UHI:uhi1"],
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
        assert "nav-track-uhi" in nav
        assert "nav-track-hi-cm" not in nav


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
        applied_milestones=["HI-CM:m1"],
    )
    workspace.refresh_from_db()
    request = rf.get("/")
    request.user = environment["applicant"]
    context = navigation_context(request, workspace)
    assert [
        (row["definition"].code, row["count"]) for row in context["nav_tracks"]
    ] == [("HI-CM", 1)]
    assert context["nav_tracks"][0]["approved"] == 0
