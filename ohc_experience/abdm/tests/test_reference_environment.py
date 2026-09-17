# ruff: noqa: F811, PLR2004
from html.parser import HTMLParser

import pytest
from django.urls import reverse

from ohc_experience.abdm.definitions import ABDM
from ohc_experience.abdm.reference import COMPOSE_FILE
from ohc_experience.abdm.reference import ABDMReferenceEnvironment
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Role
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def reference_url(workspace):
    return reverse("experiences:reference-environment", args=[workspace.reference])


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
    workspace = environment["workspace"]
    client_id = workspace.product.credential.client_id
    client.force_login(environment["applicant"])

    page = client.get(reference_url(workspace))

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


def test_m1_always_runs_and_m2_adds_its_profile(environment, client):
    client.force_login(environment["applicant"])

    page = client.get(reference_url(environment["workspace"]))

    milestones = {
        milestone.code: (option, in_progress)
        for milestone, _, option, in_progress in page.context["reference_milestones"]
    }
    assert milestones == {"M1": ("", False), "M2": ("--profile m2", True)}
    html = page.content.decode()
    assert 'name="milestones" value="m2"' in html
    assert "In progress" in html
    for shell in ("macos", "linux", "powershell"):
        assert f"{COMPOSE_FILE} --profile m2 up --build --wait --yes" in command(
            page,
            shell,
            with_hidden=True,
        )


def test_credential_fields_fill_every_command_and_are_never_submitted(
    environment,
    client,
):
    workspace = environment["workspace"]
    client.force_login(environment["applicant"])

    page = client.get(reference_url(workspace))

    fields = credential_fields(page)
    assert fields["client_id"]["value"] == workspace.product.credential.client_id
    assert "value" not in fields["client_secret"]
    assert fields["client_secret"]["placeholder"] == "YOUR_CLIENT_SECRET"
    # Inputs without a name are left out of any submission.
    assert all("name" not in field for field in fields.values())
    html = page.content.decode()
    assert "data-reference-run" in html
    assert html.count('data-credential-slot="client_id"') == 3
    assert html.count('data-credential-slot="client_secret"') == 3


def test_the_command_waits_for_credentials_that_are_not_issued(environment, client):
    workspace = environment["workspace"]
    workspace.product.credential.delete()
    client.force_login(environment["applicant"])

    page = client.get(reference_url(workspace))

    assert command(page, "macos").startswith(
        "ABDM_CLIENT_ID='YOUR_CLIENT_ID' ABDM_CLIENT_SECRET='YOUR_CLIENT_SECRET' ",
    )
    assert credential_fields(page)["client_id"]["value"] == ""
    assert "once they are issued" in page.content.decode()


def test_single_quotes_in_a_client_id_are_escaped_for_each_shell():
    shells = ABDMReferenceEnvironment.shells

    def client_id(shell):
        segments = ABDMReferenceEnvironment.command_segments(shells[shell], "it's")
        return dict(segments)["client_id"]

    assert client_id("macos") == "it'\\''s"
    assert client_id("linux") == "it'\\''s"
    assert client_id("powershell") == "it''s"


def test_support_members_have_no_reference_page(environment, client):
    workspace = environment["workspace"]
    support = UserFactory()
    Membership.objects.create(
        organisation=environment["org"],
        user=support,
        role=Role.SUPPORT,
    )
    client.force_login(support)

    assert client.get(reference_url(workspace)).status_code == 403
    overview = client.get(workspace.get_absolute_url())
    assert 'id="nav-reference"' not in main_nav(overview)


def test_other_organisations_cannot_find_the_page(environment, client):
    client.force_login(environment["outsider"])

    assert client.get(reference_url(environment["workspace"])).status_code == 404


def test_a_program_without_a_reference_environment_has_no_page(
    environment,
    client,
    monkeypatch,
):
    monkeypatch.setattr(ABDM, "reference_environment", None)
    workspace = environment["workspace"]
    client.force_login(environment["applicant"])

    assert client.get(reference_url(workspace)).status_code == 404
    overview = client.get(workspace.get_absolute_url())
    assert 'id="nav-reference"' not in main_nav(overview)
