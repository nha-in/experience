# ruff: noqa: PLR2004
import pytest
from django.test import Client
from django.urls import reverse

from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.experiences import workflows
from ohc_experience.experiences.context_processors import navigation_context
from ohc_experience.experiences.models import Product
from ohc_experience.integrations.services import provision_inline
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.support.models import Ticket
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_selected_product_follows_integrator_into_account_pages(environment):  # noqa: F811
    user = environment["applicant"]
    data = {
        **product_data(),
        "name": "Second product",
        "applied_milestones": ["PHR:p1", "PHR:p2", "PHR:p3", "PHR:p4"],
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
        assert response.context["product"].pk == second.pk
        assert second.name.encode() in response.content
        nav = (
            response.content.decode()
            .split('<nav id="app-nav"', 1)[1]
            .split("</nav>", 1)[0]
        )
        assert "nav-track-phr" in nav
        assert "nav-track-abdm" not in nav


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
    assert response.context["product"].pk == environment["product"].pk
    assert b"Private other product" not in response.content
    assert client.get(other.get_absolute_url()).status_code == 404


def test_boosted_navigation_returns_the_main_and_updated_rail(environment):  # noqa: F811
    client = Client()
    client.force_login(environment["applicant"])
    response = client.get(
        environment["product"].get_absolute_url(),
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
    product = environment["product"]
    Product.objects.filter(pk=product.pk).update(
        applied_milestones=["ABDM:m1"],
    )
    product.refresh_from_db()
    request = rf.get("/")
    request.user = environment["applicant"]
    context = navigation_context(request, product)
    assert [
        (row["definition"].code, row["count"]) for row in context["nav_tracks"]
    ] == [("ABDM", 1)]
    assert context["nav_tracks"][0]["approved"] == 0


def test_a_track_counts_only_the_milestones_the_product_applied_for(environment, rf):  # noqa: F811
    """UHI shows M2 as related context, but a product without it counts 2, not 3."""
    product, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data={
            **product_data("M1 and UHI only"),
            "applied_milestones": ["ABDM:m1", "UHI:uhi1"],
        },
    )
    assert product, form.errors
    provision_inline(product)
    product.refresh_from_db()
    assert not product.milestones.filter(key="m2").exists()

    request = rf.get("/")
    request.user = environment["applicant"]
    counts = {
        row["definition"].code: (row["approved"], row["count"])
        for row in navigation_context(request, product)["nav_tracks"]
    }

    assert counts == {"ABDM": (0, 1), "UHI": (0, 2)}


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


def test_onboarding_offers_the_product_step_on_the_organisation_page(
    client,
    owner_membership,
):
    client.force_login(owner_membership.user)

    html = client.get(reverse("experiences:organisation")).content.decode()

    assert "Continue to product" in html


def test_the_product_step_goes_once_the_organisation_has_a_product(environment):  # noqa: F811
    """Editing an approved organisation is a settings visit, not registration."""
    client = Client()
    client.force_login(environment["applicant"])

    response = client.get(reverse("experiences:organisation"))

    assert response.status_code == 200
    assert response.context["can_edit"], "the form still takes edits after approval"
    assert "Continue to product" not in response.content.decode()


def app_nav(client, url):
    """The sidebar of the page at `url`, its whitespace collapsed."""
    html = client.get(url).content.decode()
    return " ".join(html.split('<nav id="app-nav"', 1)[1].split("</nav>", 1)[0].split())


def test_the_support_count_says_what_it_counts(environment):  # noqa: F811
    """Tickets with the NHA team or awaiting the integrator; resolved ones drop off."""
    for status in ("open", "awaiting_integrator", "closed"):
        Ticket.objects.create(
            organisation=environment["org"],
            product=environment["product"],
            created_by=environment["applicant"],
            subject=f"A ticket {status}",
            status=status,
        )
    client = Client()
    client.force_login(environment["admin"])

    nav = app_nav(client, reverse("experiences:assess-dashboard"))

    assert (
        '<span class="font-mono text-[11px] text-soft-foreground" '
        'title="2 open tickets">2<span class="sr-only"> open tickets</span></span>'
    ) in nav
    Ticket.objects.filter(status="open").update(status="closed")
    client.force_login(environment["applicant"])
    nav = app_nav(client, environment["product"].get_absolute_url())
    assert 'title="1 open ticket">1<span class="sr-only"> open ticket</span>' in nav


def test_documentation_says_it_opens_in_a_new_tab(environment):  # noqa: F811
    client = Client()
    client.force_login(environment["applicant"])

    nav = app_nav(client, environment["product"].get_absolute_url())

    docs = nav[nav.index('<a id="nav-docs"') :].split("</a>", 1)[0]
    assert 'target="_blank"' in docs
    assert docs.endswith(
        'Documentation <span aria-hidden="true">↗&#xFE0E;</span>'
        '<span class="sr-only"> (opens in a new tab)</span>',
    )


def test_the_profile_sits_with_the_account_and_staff_under_administration(
    environment,  # noqa: F811
):
    client = Client()
    client.force_login(environment["admin"])

    nav = app_nav(client, reverse("experiences:assess-dashboard"))

    links, rest = nav.split('class="mt-auto', 1)
    bottom, account = rest.split("</ul>", 1)
    heading = (
        '<li class="px-2 pt-5 pb-2 text-[11px] font-bold tracking-[0.09em] '
        'text-soft-foreground uppercase">Administration</li>'
    )
    assert heading in links
    assert links.index(heading) < links.index('id="nav-staff"')
    assert links.index("Programme</li>") < links.index(heading)
    assert 'id="nav-staff"' not in bottom
    assert 'id="nav-docs"' in bottom
    assert 'id="nav-profile"' not in links + bottom
    profile = account[account.index('<a id="nav-profile"') :].split("</a>", 1)[0]
    assert f'href="{reverse("users:profile")}"' in profile
    assert 'title="Your profile"' in profile
    assert '<span class="sr-only">Your profile: </span>' in profile
    assert environment["admin"].display_name in profile
    assert environment["admin"].email in profile

    # A reviewer has nothing to administer; an integrator's team stays put.
    client.force_login(environment["reviewer"])
    nav = app_nav(client, reverse("experiences:assess-dashboard"))
    assert "Administration" not in nav
    assert 'id="nav-staff"' not in nav
    client.force_login(environment["applicant"])
    nav = app_nav(client, environment["product"].get_absolute_url())
    bottom = nav.split('class="mt-auto', 1)[1].split("</ul>", 1)[0]
    assert 'id="nav-settings"' in bottom
    assert "Administration" not in nav
    assert 'id="nav-profile"' in nav.split("</ul>")[-1]
