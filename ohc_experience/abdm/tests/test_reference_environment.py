# ruff: noqa: F811, PLR2004
from html.parser import HTMLParser

import pytest
from django.urls import reverse

from ohc_experience.abdm.definitions import ABDM
from ohc_experience.abdm.reference import COMPOSE_FILE
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


def test_integrators_get_run_commands_with_their_client_id(environment, client):
    workspace = environment["workspace"]
    client_id = workspace.product.credential.client_id
    client.force_login(environment["applicant"])

    page = client.get(reference_url(workspace))

    assert page.status_code == 200
    assert command(page, "posix") == (
        f"ABDM_CLIENT_ID={client_id} ABDM_CLIENT_SECRET=YOUR_CLIENT_SECRET "
        f"docker compose -f {COMPOSE_FILE} up --build --wait --yes"
    )
    assert command(page, "powershell") == (
        f'$env:ABDM_CLIENT_ID="{client_id}"; '
        '$env:ABDM_CLIENT_SECRET="YOUR_CLIENT_SECRET"; '
        f"docker compose -f {COMPOSE_FILE} up --build --wait --yes"
    )
    html = page.content.decode()
    assert 'data-copy-from="reference-command-posix"' in html
    assert 'data-copy-from="reference-command-powershell"' in html
    assert "http://localhost:4400" in html
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
    for shell in ("posix", "powershell"):
        assert command(page, shell, with_hidden=True).endswith(
            f"{COMPOSE_FILE} --profile m2 up --build --wait --yes",
        )


def test_the_command_waits_for_credentials_that_are_not_issued(environment, client):
    workspace = environment["workspace"]
    workspace.product.credential.delete()
    client.force_login(environment["applicant"])

    page = client.get(reference_url(workspace))

    assert command(page, "posix").startswith(
        "ABDM_CLIENT_ID=YOUR_CLIENT_ID ABDM_CLIENT_SECRET=YOUR_CLIENT_SECRET ",
    )
    assert "once they appear in" in page.content.decode()


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
