# ruff: noqa: F811, PLR2004
import base64
import json
from html.parser import HTMLParser
from urllib.parse import unquote

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from ohc_experience.abdm.definitions import ABDM
from ohc_experience.abdm.skills import ABDMAgentSkills
from ohc_experience.abdm.skills import ABDMDocsMcp
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.experiences.definitions import DocsMcpDefinition
from ohc_experience.experiences.definitions import McpTarget

pytestmark = pytest.mark.django_db

TEST_URL = "https://docs-mcp.test.example/mcp"


def skills_url(workspace):
    return reverse("experiences:agent-skills", args=[workspace.reference])


class DisabledButtons(HTMLParser):
    """Every "Add to X" control on the page, whether link or disabled button."""

    def __init__(self):
        super().__init__()
        self.controls = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        label = a.get("aria-label", "")
        if tag in ("a", "button") and label.startswith("Add to "):
            self._current = {
                "tag": tag,
                "disabled": "disabled" in a,
                "href": a.get("href"),
            }
            self.controls.append(self._current)


def add_to_controls(response):
    parser = DisabledButtons()
    parser.feed(response.content.decode())
    return parser.controls


def test_the_panel_shows_a_link_per_agent_and_points_to_the_docs_site(
    environment,
    client,
    settings,
):
    settings.ABDM_MCP_URL = TEST_URL
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    assert page.status_code == 200
    html = page.content.decode()
    assert ">Docs MCP server</h2>" in html
    controls = add_to_controls(page)
    # One control per target, in the order `targets` lists them, none disabled.
    assert [c["tag"] for c in controls] == ["a", "a", "a"]
    assert not any(c["disabled"] for c in controls)
    # What the server can do is not repeated here; the panel sends a reader
    # to the same page that documents both the skill and the server.
    assert f'href="{ABDMAgentSkills.docs_url()}"' in html


def test_the_cli_command_and_generic_config_name_the_live_endpoint(
    environment,
    client,
    settings,
):
    settings.ABDM_MCP_URL = TEST_URL
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    html = page.content.decode()
    assert f"claude mcp add --transport http abdm-docs {TEST_URL} -s user" in html
    # The generic block is HTML-escaped inside its <pre>; the browser's
    # innerText (what the copy button reads) unescapes it back to plain JSON.
    assert "mcpServers" in html
    assert "abdm-docs" in html
    assert TEST_URL in html
    assert 'data-copy-from="docs-mcp-command-cli"' in html
    assert 'data-copy-from="docs-mcp-command-generic"' in html


def test_each_agent_gets_the_link_its_scheme_expects(environment, client, settings):
    settings.ABDM_MCP_URL = TEST_URL
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    controls = add_to_controls(page)
    hrefs = [c["href"] for c in controls]
    claude = next(h for h in hrefs if h.startswith("claude://"))
    cursor = next(h for h in hrefs if h.startswith("cursor://"))
    vscode = next(h for h in hrefs if h.startswith("vscode:"))
    # Claude Code lands the add command in the composer, unrun.
    prompt = unquote(claude.split("q=", 1)[1])
    assert f"claude mcp add --transport http abdm-docs {TEST_URL} -s user" in prompt
    # Cursor takes a base64 JSON config naming only the endpoint.
    config = cursor.split("config=", 1)[1]
    assert json.loads(base64.b64decode(unquote(config))) == {"url": TEST_URL}
    # VS Code takes the whole install payload as its query string.
    assert json.loads(unquote(vscode.split("?", 1)[1])) == {
        "name": "abdm-docs",
        "type": "http",
        "url": TEST_URL,
    }


def test_with_no_address_the_panel_shows_locked_controls_instead_of_a_dead_link(
    environment,
    client,
    settings,
):
    settings.ABDM_MCP_URL = ""
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    html = page.content.decode()
    assert "Address not set for this deployment" in html
    controls = add_to_controls(page)
    assert [c["tag"] for c in controls] == ["button", "button", "button"]
    assert all(c["disabled"] for c in controls)
    # The copy boxes still read as a command and a config, not a dead end;
    # HTML-escaped angle brackets still copy back to "<...>" in the browser.
    assert "mcp-url, set at deploy" in html


def test_a_program_without_a_docs_mcp_server_shows_no_panel(
    environment,
    client,
    monkeypatch,
):
    monkeypatch.setattr(ABDM, "docs_mcp", None)
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    assert "Docs MCP server" not in page.content.decode()
    assert page.context["mcp_targets"] == []


def test_only_claude_cursor_and_vscode_build_a_deeplink():
    build = ABDMDocsMcp.deeplink

    assert build("claude").startswith("claude://")
    assert build("cursor").startswith("cursor://")
    assert build("vscode").startswith("vscode:")
    # A key this server does not target builds nothing, live endpoint or not.
    assert build("codex") is None


def test_deeplink_is_none_with_no_live_endpoint(settings):
    settings.ABDM_MCP_URL = ""

    assert ABDMDocsMcp.deeplink("claude") is None
    assert ABDMDocsMcp.deeplink("cursor") is None
    assert ABDMDocsMcp.deeplink("vscode") is None


def test_validate_requires_a_name_and_a_setting():
    class NoName(DocsMcpDefinition):
        name = ""
        url_setting = "ABDM_MCP_URL"

    class NoSetting(DocsMcpDefinition):
        name = "abdm-docs"
        url_setting = ""

    with pytest.raises(ImproperlyConfigured, match="needs a name"):
        NoName.validate()
    with pytest.raises(ImproperlyConfigured, match="needs a name"):
        NoSetting.validate()


def test_validate_refuses_a_target_deeplink_does_not_know():
    class Unknown(DocsMcpDefinition):
        name = "abdm-docs"
        url_setting = "ABDM_MCP_URL"
        targets = {"codex": McpTarget("Codex")}

    with pytest.raises(ImproperlyConfigured, match="codex"):
        Unknown.validate()


def test_every_target_carries_a_note_shown_as_its_button_tooltip(
    environment,
    client,
    settings,
):
    """Unlike the install command, all three links show at once, so the note
    rides along on each one rather than switching with a selection."""
    settings.ABDM_MCP_URL = TEST_URL
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    html = page.content.decode()
    for target in ABDMDocsMcp.targets.values():
        assert target.note
        assert f'title="{target.note}"' in html


def test_the_deployment_decides_which_mcp_server_is_offered(
    environment,
    client,
    settings,
):
    # A staging portal handing out a production endpoint would be the wrong one.
    settings.ABDM_MCP_URL = "https://mcp.staging.example/mcp"
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    assert ABDMDocsMcp.url() == "https://mcp.staging.example/mcp"
    assert "https://mcp.staging.example/mcp" in page.content.decode()
