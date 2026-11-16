# ruff: noqa: F811
from http import HTTPStatus

import pytest
from django.urls import reverse

from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.experiences import global_search
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.support.models import Ticket

pytestmark = pytest.mark.django_db

LEGAL_NAME = "Medibase Technologies Private Limited"


def named(organisation):
    """The fixture's organisation, trading as "Test health systems" under a legal
    name, which is the name its rows show."""
    organisation.name = "Test health systems"
    organisation.legal_name = LEGAL_NAME
    organisation.save(update_fields=["name", "legal_name"])
    return organisation


def reached(owner, phone_number="+919000010001"):
    owner.phone_number = phone_number
    owner.save(update_fields=["phone_number"])
    return owner


def rows_of(client, url, **params):
    """A list's response, and the rows on its page."""
    response = client.get(url, params)
    assert response.status_code == HTTPStatus.OK
    rows = "organizations" if "organizations" in url else "products"
    return response, list(response.context[rows])


@pytest.mark.parametrize(
    ("text", "digits"),
    [
        ("+91 90000 10001", "9000010001"),
        ("90000-10001", "9000010001"),
        ("(90000) 10001", "9000010001"),
        ("9000010001", "9000010001"),
        ("90000", "90000"),
        ("2026", ""),
        ("ravi@example.com", ""),
        ("PRD-2026-00012", ""),
    ],
)
def test_a_number_in_every_field_is_searched_by_its_last_ten_digits(text, digits):
    assert global_search.mobile_digits(text) == digits


def test_organisations_list_finds_a_member_by_email_or_mobile(environment, client):
    owner = reached(environment["applicant"])
    organisation = named(environment["org"])
    client.force_login(environment["reviewer"])
    url = reverse("experiences:organizations")

    for text, contact in (
        (owner.email.upper(), owner.email),
        ("90000 10001", owner.phone_number),
        ("10001", owner.phone_number),
    ):
        response, [row] = rows_of(client, url, q=text)
        assert row == organisation
        assert row.search_match == f"member {owner.display_name} · {contact}"
        assert "Matched:" in response.content.decode()

    # A row that shows what matched says nothing more.
    _, [row] = rows_of(client, url, q="medibase")
    assert row.search_match == ""
    # The name it trades under is not what the row shows.
    _, [row] = rows_of(client, url, q="test health")
    assert row.search_match == "name Test health systems"
    assert "Matched:" not in client.get(url).content.decode()


def test_organisations_list_searches_one_field_when_asked(environment, client):
    owner = reached(environment["applicant"])
    organisation = named(environment["org"])
    client.force_login(environment["reviewer"])
    url = reverse("experiences:organizations")

    for text, field, match in (
        ("medibase", "name", ""),
        ("medibase", "email", None),
        (owner.email, "email", f"member {owner.display_name} · {owner.email}"),
        (owner.email, "name", None),
        ("10001", "mobile", f"member {owner.display_name} · {owner.phone_number}"),
        ("10001", "email", None),
        # A field the list does not have is every field.
        ("medibase", "nope", ""),
    ):
        _, found = rows_of(client, url, q=text, field=field)
        if match is None:
            assert found == [], (text, field)
            continue
        assert found == [organisation], (text, field)
        assert found[0].search_match == match, (text, field)
    page = client.get(url, {"q": "medibase", "field": "email"}).content.decode()
    assert '<option value="email" selected>Email</option>' in page


def test_products_list_finds_client_ids_and_the_legacy_application_id(
    environment,
    client,
):
    product = environment["product"]
    product.production_client_id = "PROD-00471"
    product.metadata = {**product.metadata, "legacy_application_id": "1004417"}
    product.save(update_fields=["production_client_id", "metadata"])
    sandbox = product.credential.client_id
    named(environment["org"])
    client.force_login(environment["reviewer"])
    url = reverse("experiences:products")

    for text, match in (
        (sandbox.lower(), f"Sandbox ID {sandbox}"),
        ("prod-00471", "Production ID PROD-00471"),
        ("1004417", "legacy application ID 1004417"),
        ("test health", "organisation Test health systems"),
        ("medibase hmis", ""),
        (product.reference, ""),
    ):
        _, [row] = rows_of(client, url, q=text)
        assert row == product, text
        assert row.search_match == match, text


def test_products_list_searches_one_field_when_asked(environment, client):
    product = environment["product"]
    product.production_client_id = "PROD-00471"
    product.save(update_fields=["production_client_id"])
    sandbox = product.credential.client_id
    named(environment["org"])
    client.force_login(environment["reviewer"])
    url = reverse("experiences:products")

    for text, field, match in (
        (sandbox, "sandbox", f"Sandbox ID {sandbox}"),
        (sandbox, "product", None),
        ("prod-00471", "production", "Production ID PROD-00471"),
        ("prod-00471", "sandbox", None),
        (product.reference, "product", ""),
        ("medibase hmis", "product", ""),
        # The organisation's name is in every field, not in Product.
        ("test health", "product", None),
    ):
        _, found = rows_of(client, url, q=text, field=field)
        if match is None:
            assert found == [], (text, field)
            continue
        assert found == [product], (text, field)
        assert found[0].search_match == match, (text, field)


def test_each_row_opens_its_list_searched_in_its_field(environment, client):
    client.force_login(environment["reviewer"])
    response = client.get(reverse("experiences:organizations"))
    rows = response.context["search_rows"]

    assert [row.label for row in rows] == [
        "Organisation",
        "Product",
        "Sandbox ID",
        "Production ID",
        "Email",
        "Mobile",
        "Review request",
        "Ticket",
    ]
    page = response.content.decode()
    for row in rows:
        assert f'data-url="{row.url}"' in page
        assert client.get(global_search.searched(row.url, "medibase")).status_code == (
            HTTPStatus.OK
        )
    organisations = client.get(global_search.searched(rows[4].url, "medibase"))
    assert organisations.context["search_field"] == "email"
    products = client.get(global_search.searched(rows[2].url, "medibase"))
    assert products.context["search_field"] == "sandbox"


def test_the_queue_and_ticket_rows_open_on_their_all_tabs(environment, client):
    ticket = Ticket.objects.create(
        organisation=environment["org"],
        product=environment["product"],
        created_by=environment["applicant"],
        subject="Callback fails",
        status="closed",
    )
    client.force_login(environment["reviewer"])
    rows = {
        row.label: row
        for row in client.get(reverse("experiences:organizations")).context[
            "search_rows"
        ]
    }

    page = client.get(global_search.searched(rows["Ticket"].url, "callback"))
    assert ticket.reference in page.content.decode()
    # Support opens on Needs a reply, which leaves a resolved ticket out.
    support = client.get(reverse("experiences:support"), {"q": "callback"})
    assert ticket.reference not in support.content.decode()
    assert global_search.searched(rows["Review request"].url, "care") == (
        f"{reverse('experiences:queue')}?scope=all&q=care"
    )


def test_the_ticket_row_is_for_staff_who_read_support(environment, client):
    reviewer = environment["reviewer"]
    AccessGrant.objects.filter(user=reviewer, area="support").delete()
    client.force_login(reviewer)

    rows = client.get(reverse("experiences:organizations")).context["search_rows"]

    assert "Ticket" not in [row.label for row in rows]


def test_without_javascript_the_search_opens_its_first_row(environment, client):
    client.force_login(environment["reviewer"])
    url = reverse("experiences:search")

    response = client.get(url, {"q": "care"})
    assert response.status_code == HTTPStatus.FOUND
    assert response.url == f"{reverse('experiences:organizations')}?field=name&q=care"
    assert client.get(url).url == reverse("experiences:organizations")


def test_only_staff_who_read_reviews_have_the_search(environment, client):
    client.force_login(environment["applicant"])
    response = client.get(reverse("experiences:search"), {"q": "medibase"})
    assert response.status_code == HTTPStatus.FORBIDDEN
    page = client.get(reverse("experiences:home")).content.decode()
    assert "data-global-search" not in page

    client.force_login(environment["reviewer"])
    page = client.get(reverse("experiences:organizations")).content.decode()
    # The top bar's box, and the phone drawer's copy.
    assert 'id="global-search"' in page
    assert 'id="global-search-drawer"' in page
    # Only the top bar's has the "/" key: a phone has no keys.
    assert page.count('aria-keyshortcuts="/"') == 1
