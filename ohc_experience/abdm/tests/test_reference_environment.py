# ruff: noqa: F811, PLR2004
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from django.urls import reverse

from ohc_experience.abdm.definitions import ABDM
from ohc_experience.abdm.reference import COMPOSE_FILE
from ohc_experience.abdm.reference import ABDMReferenceEnvironment
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import stored_secret
from ohc_experience.experiences import credentials
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Role
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def reference_url(product):
    return reverse("experiences:reference-environment", args=[product.reference])


def main_nav(response):
    html = response.content.decode()
    return html.split('<nav id="app-nav"', 1)[1].split('class="mt-auto', 1)[0]


class CommandText(HTMLParser):
    """An element's text as the copy button reads it, optionally with hidden parts."""

    def __init__(self, element_id, *, with_hidden):
        super().__init__()
        self.element_id = element_id
        self.with_hidden = with_hidden
        self.depth = 0
        self.hidden_depth = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.depth:
            self.depth += 1
            hides = "hidden" in attributes and not self.with_hidden
            if hides and self.hidden_depth is None:
                self.hidden_depth = self.depth
        elif attributes.get("id") == self.element_id:
            self.depth = 1

    def handle_endtag(self, tag):
        if self.depth:
            if self.hidden_depth == self.depth:
                self.hidden_depth = None
            self.depth -= 1

    def handle_data(self, data):
        if self.depth and self.hidden_depth is None:
            self.parts.append(data)


def command(response, shell, *, with_hidden=False):
    parser = CommandText(f"reference-command-{shell}", with_hidden=with_hidden)
    parser.feed(response.content.decode())
    return " ".join("".join(parser.parts).split())


class CredentialFields(HTMLParser):
    """The attributes of the inputs that fill credentials into the commands."""

    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and "data-reference-credential" in attributes:
            self.fields[attributes["data-reference-credential"]] = attributes


def credential_fields(response):
    parser = CredentialFields()
    parser.feed(response.content.decode())
    return parser.fields


RUN = f"docker compose -f {COMPOSE_FILE} up --build --wait --yes"


def test_integrators_get_run_commands_that_open_care_when_it_is_ready(
    environment,
    client,
):
    product = environment["product"]
    client_id = product.credential.client_id
    client.force_login(environment["applicant"])

    page = client.get(reference_url(product))

    assert page.status_code == 200
    credentials = (
        f"ABDM_CLIENT_ID='{client_id}' ABDM_CLIENT_SECRET='YOUR_CLIENT_SECRET'"
    )
    assert command(page, "macos") == (
        f"{credentials} {RUN} && open http://localhost:4400"
    )
    assert command(page, "linux") == (
        f"{credentials} {RUN} && xdg-open http://localhost:4400"
    )
    assert command(page, "powershell") == (
        f"$env:ABDM_CLIENT_ID='{client_id}'; "
        "$env:ABDM_CLIENT_SECRET='YOUR_CLIENT_SECRET'; "
        f"{RUN}; if ($LASTEXITCODE -eq 0) {{ Start-Process http://localhost:4400 }}"
    )
    html = page.content.decode()
    for shell in ("macos", "linux", "powershell"):
        assert f'data-copy-from="reference-command-{shell}"' in html
    assert "docker compose -p care-reference down" in html
    assert "Health record request and data transfer" in html
    assert 'alt="Open Healthcare Network"' in html
    assert 'alt="eGov Foundation"' in html
    assert "Digital Public Good · MIT licensed" in html
    link = main_nav(page).split('id="nav-reference"', 1)[1].split(">", 1)[0]
    assert 'aria-current="page"' in link


def test_m1_always_runs_and_m2_to_m4_share_one_choice(environment, client):
    client.force_login(environment["applicant"])

    page = client.get(reference_url(environment["product"]))

    rows = {
        tuple(milestone.code for milestone in milestones): (option, in_progress)
        for milestones, option, in_progress in page.context["reference_milestones"]
    }
    assert rows == {
        ("M1",): ("", False),
        ("M2", "M3", "M4"): ("--profile m2", False),
    }
    html = page.content.decode()
    assert html.count('name="milestones"') == 1
    assert 'name="milestones" value="m2"' in html
    for key in ("m3", "m4"):
        assert ABDM.milestones[key].name in html
    assert "In progress" not in html
    for shell in ("macos", "linux", "powershell"):
        assert f"{COMPOSE_FILE} --profile m2 up --build --wait --yes" in command(
            page,
            shell,
            with_hidden=True,
        )


def test_the_flows_card_links_to_the_milestone_documentation(environment, client):
    client.force_login(environment["applicant"])

    page = client.get(reference_url(environment["product"]))

    card = page.content.decode().split('id="reference-flows-title"', 1)[1]
    card = card.split("</section>", 1)[0]
    assert f'href="{ABDM.milestones_docs_url}"' in card
    for key in ("m1", "m2", "m3", "m4"):
        assert f'href="{ABDM.milestones[key].docs_url}"' in card
    assert "Consent request by ABHA address" in card
    assert "HPID creation and HPR registration" in card


def test_credential_fields_fill_every_command_and_are_never_submitted(
    environment,
    client,
):
    product = environment["product"]
    client.force_login(environment["applicant"])

    page = client.get(reference_url(product))

    fields = credential_fields(page)
    assert fields["client_id"]["value"] == product.credential.client_id
    assert "value" not in fields["client_secret"]
    assert fields["client_secret"]["placeholder"] == "YOUR_CLIENT_SECRET"
    # Inputs without a name are left out of any submission.
    assert all("name" not in field for field in fields.values())
    html = page.content.decode()
    assert "data-reference-run" in html
    assert html.count('data-credential-slot="client_id"') == 3
    assert html.count('data-credential-slot="client_secret"') == 3
    assert stored_secret(product) not in html


def test_fill_in_secret_is_offered_while_the_credential_is_active(
    environment,
    client,
):
    product = environment["product"]
    client.force_login(environment["applicant"])

    page = client.get(reference_url(product))

    # Once filled in, the page holds the secret, so no cache keeps a copy.
    assert "no-store" in page["Cache-Control"]
    html = page.content.decode()
    assert 'id="reference-secret-fill"' in html
    assert 'form="reference-secret-form"' in html
    assert 'id="reference-secret-form"' in html
    assert "Fill in your secret, or copy it from" in html
    ProductCredential.objects.filter(product=product).update(status="revoked")
    html = client.get(reference_url(product)).content.decode()
    assert 'id="reference-secret-fill"' not in html
    assert 'id="reference-secret-form"' not in html
    assert "the secret is in" in html


def test_fill_in_secret_swaps_in_the_field_holding_the_secret(environment, client):
    product = environment["product"]
    client.force_login(environment["applicant"])

    response = client.post(reference_url(product), **HTMX)

    assert response.status_code == 200
    fields = credential_fields(response)
    assert set(fields) == {"client_secret"}
    assert fields["client_secret"]["value"] == stored_secret(product)
    assert "autofocus" in fields["client_secret"]
    html = response.content.decode()
    assert 'id="reference-secret-fill"' not in html
    assert 'id="reference-command-macos"' not in html
    assert 'hx-swap-oob="innerHTML"' in html
    assert "Client secret filled in." in html
    assert "no-store" in response["Cache-Control"]
    assert AuditEvent.objects.filter(action="Client secret revealed").count() == 1


def test_fill_in_secret_without_javascript_fills_every_command(environment, client):
    product = environment["product"]
    secret = stored_secret(product)
    client.force_login(environment["applicant"])

    page = client.post(reference_url(product))

    assert page.status_code == 200
    assert credential_fields(page)["client_secret"]["value"] == secret
    assert f"ABDM_CLIENT_SECRET='{secret}' " in command(page, "macos")
    assert f"ABDM_CLIENT_SECRET='{secret}' " in command(page, "linux")
    assert f"$env:ABDM_CLIENT_SECRET='{secret}';" in command(page, "powershell")
    assert "Client secret filled in." in page.content.decode()
    assert "no-store" in page["Cache-Control"]


def test_fills_count_against_the_reveal_limit_and_a_refusal_says_why(
    environment,
    client,
    monkeypatch,
):
    # One rate-limit window throughout, so the count cannot reset mid-test.
    monkeypatch.setattr(credentials, "time", SimpleNamespace(time=lambda: 1.8e9))
    product = environment["product"]
    client.force_login(environment["applicant"])
    for _ in range(5):
        filled = client.post(reference_url(product), **HTMX)
        assert "value" in credential_fields(filled)["client_secret"]

    response = client.post(reference_url(product), **HTMX)

    assert response.status_code == 200
    assert "value" not in credential_fields(response)["client_secret"]
    html = response.content.decode()
    assert 'id="reference-secret-fill"' in html
    assert "Too many requests. Wait a minute and try again." in html
    assert stored_secret(product) not in html
    page = client.post(reference_url(product))
    assert page.status_code == 302
    assert page.url == reference_url(product)


def test_a_credential_revoked_meanwhile_is_not_filled_in(environment, client):
    product = environment["product"]
    client.force_login(environment["applicant"])
    ProductCredential.objects.filter(product=product).update(status="revoked")

    response = client.post(reference_url(product), **HTMX)

    assert response.status_code == 200
    assert "value" not in credential_fields(response)["client_secret"]
    html = response.content.decode()
    assert 'id="reference-secret-fill"' not in html
    assert "These credentials are no longer active." in html
    assert not AuditEvent.objects.filter(action="Client secret revealed").exists()


def test_the_command_waits_for_credentials_that_are_not_issued(environment, client):
    product = environment["product"]
    product.credential.delete()
    client.force_login(environment["applicant"])

    page = client.get(reference_url(product))

    assert command(page, "macos").startswith(
        "ABDM_CLIENT_ID='YOUR_CLIENT_ID' ABDM_CLIENT_SECRET='YOUR_CLIENT_SECRET' ",
    )
    assert credential_fields(page)["client_id"]["value"] == ""
    html = page.content.decode()
    assert "once they are issued" in html
    assert 'id="reference-secret-fill"' not in html
    assert client.post(reference_url(product), **HTMX).status_code == 404


def test_single_quotes_in_credentials_are_escaped_for_each_shell():
    shells = ABDMReferenceEnvironment.shells

    def escaped(shell):
        segments = dict(
            ABDMReferenceEnvironment.command_segments(shells[shell], "it's", "o'k"),
        )
        return segments["client_id"], segments["client_secret"]

    assert escaped("macos") == ("it'\\''s", "o'\\''k")
    assert escaped("linux") == ("it'\\''s", "o'\\''k")
    assert escaped("powershell") == ("it''s", "o''k")


def test_support_members_have_no_reference_page(environment, client):
    product = environment["product"]
    support = UserFactory()
    Membership.objects.create(
        organisation=environment["org"],
        user=support,
        role=Role.SUPPORT,
    )
    client.force_login(support)

    assert client.get(reference_url(product)).status_code == 403
    assert client.post(reference_url(product), **HTMX).status_code == 403
    assert not AuditEvent.objects.filter(action="Client secret revealed").exists()
    overview = client.get(product.get_absolute_url())
    assert 'id="nav-reference"' not in main_nav(overview)


def test_other_organisations_cannot_find_the_page(environment, client):
    client.force_login(environment["outsider"])

    assert client.get(reference_url(environment["product"])).status_code == 404
    assert client.post(reference_url(environment["product"])).status_code == 404


def test_a_program_without_a_reference_environment_has_no_page(
    environment,
    client,
    monkeypatch,
):
    monkeypatch.setattr(ABDM, "reference_environment", None)
    product = environment["product"]
    client.force_login(environment["applicant"])

    assert client.get(reference_url(product)).status_code == 404
    overview = client.get(product.get_absolute_url())
    assert 'id="nav-reference"' not in main_nav(overview)
