# ruff: noqa: F811, PLR2004
from html.parser import HTMLParser
from unittest import mock
from urllib.parse import unquote

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from ohc_experience.abdm.definitions import ABDM
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.skills import ABDMAgentSkills
from ohc_experience.abdm.tests.test_reference_environment import CommandText
from ohc_experience.abdm.tests.test_reference_environment import main_nav
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.experiences import workflows
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Role
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PUBLISHED = ABDMAgentSkills.skills_url()
CLAUDE_SCHEME = "claude://code/new?q="
CURSOR_SCHEME = "cursor://anysphere.cursor-deeplink/prompt?text="


def skills_url(workspace):
    return reverse("experiences:agent-skills", args=[workspace.reference])


def command(response, target):
    """One agent's install command, as the copy button reads it."""
    parser = CommandText(f"agent-skill-command-{target}", with_hidden=False)
    parser.feed(response.content.decode())
    return " ".join("".join(parser.parts).split())


class DeepLinks(HTMLParser):
    """Every install deeplink on the page, by the skill it opens and its state."""

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        href = a.get("href", "")
        if tag == "a" and a.get("data-show-when-field") == "skill" and "://" in href:
            self.links.append(
                {
                    "href": href,
                    "slug": a.get("data-show-when-value"),
                    "hidden": "hidden" in a,
                },
            )


def deeplinks(response, scheme):
    """The deeplinks for one agent scheme, in the order the page renders them."""
    parser = DeepLinks()
    parser.feed(response.content.decode())
    return [link for link in parser.links if link["href"].startswith(scheme)]


class AgentTabs(HTMLParser):
    """The agent tabs in page order, each with whether it opens checked."""

    def __init__(self):
        super().__init__()
        self.tabs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("name") == "agent":
            self.tabs.append((a.get("value"), "checked" in a))


class DocsLinks(HTMLParser):
    """Each card's Docs link, by the skill title it names."""

    def __init__(self):
        super().__init__()
        self.links = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        label = a.get("aria-label") or ""
        if tag == "a" and label.startswith("Docs for "):
            self.links[label.removeprefix("Docs for ")] = a["href"]


def a_skill(slug):
    return next(skill for skill in ABDMAgentSkills.skills() if skill["slug"] == slug)


def cards(response):
    """Every skill card, by the folder the card installs."""
    return {
        row["definition"]["slug"]: row
        for group in response.context["skill_groups"]
        for row in group["skills"]
    }


def groups(response):
    return {group["code"]: group for group in response.context["skill_groups"]}


def hie_cm_only(environment, name="HIE-CM only"):
    """A product that applied for one track only."""
    workspace, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data=product_data(name)
        | {"applied_milestones": ["HIE-CM:m1", "HIE-CM:m2", "HIE-CM:m3"]},
    )
    assert workspace, form.errors
    return workspace


def install(target, slug, sections):
    """The command the page shows for one agent and one skill, as copied."""
    directory = ABDMAgentSkills.targets[target].directory
    return (
        f"mkdir -p {directory}{slug}/references"
        f" && curl -fsSL {PUBLISHED}/{slug}/SKILL.md -o {directory}{slug}/SKILL.md"
        f" && for f in {' '.join(sections)}; do"
        f" curl -fsSL {PUBLISHED}/{slug}/references/$f.md"
        f" -o {directory}{slug}/references/$f.md; done"
    )


M1_SECTIONS = ("scaffold", "integrate", "debug")


def test_each_agent_gets_the_command_that_fetches_the_chosen_skill(
    environment,
    client,
):
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    assert page.status_code == 200
    # A skill is a folder, so the command takes SKILL.md and the sections it
    # routes to. Every agent fetches the same folder: only the directory moves.
    for target in ("claude", "cursor", "codex", "copilot"):
        assert command(page, target) == install(target, "abdm-m1", M1_SECTIONS)
        assert f'data-copy-from="agent-skill-command-{target}"' in page.content.decode()
    html = page.content.decode()
    assert "# installs into .claude/skills/" in html
    assert "a debug loop stops after five passes and asks" in html
    assert f'href="{ABDMAgentSkills.docs_url()}"' in html
    link = main_nav(page).split('id="nav-skills"', 1)[1].split(">", 1)[0]
    assert 'aria-current="page"' in link


def test_copilot_is_the_first_agent_and_the_one_the_page_opens_on(
    environment,
    client,
):
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    tabs = AgentTabs()
    tabs.feed(page.content.decode())
    assert tabs.tabs == [
        ("copilot", True),
        ("codex", False),
        ("cursor", False),
        ("claude", False),
    ]


def test_the_command_fetches_only_the_sections_the_chosen_skill_has(
    environment,
    client,
):
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    # FHIR carries two sections rather than the usual three, and the command has
    # to name its own, or the loop fetches files that are not published.
    html = page.content.decode()
    assert (
        install("claude", "abdm-fhir", ("generate", "audit"))
        .split(" && for f in ")[1]
        .startswith("generate audit;")
    )
    assert ">generate audit</span>" in html
    assert ">scaffold integrate debug</span>" in html


def test_claude_and_cursor_get_a_one_click_link_for_the_chosen_skill(
    environment,
    client,
):
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    installable = [skill["slug"] for skill in page.context["installable_skills"]]
    claude = deeplinks(page, CLAUDE_SCHEME)
    cursor = deeplinks(page, CURSOR_SCHEME)
    # Claude Code and Cursor each open a link, one per skill the command offers.
    assert [link["slug"] for link in claude] == installable
    assert [link["slug"] for link in cursor] == installable
    # Only the chosen skill's link shows, the same as its command does.
    assert [link["slug"] for link in claude if not link["hidden"]] == ["abdm-m1"]
    assert page.context["selected_skill"] == "abdm-m1"
    # The link lands the command the copy button holds in the composer, unrun.
    m1 = next(link for link in claude if link["slug"] == "abdm-m1")
    prompt = unquote(m1["href"].split("q=", 1)[1])
    assert install("claude", "abdm-m1", M1_SECTIONS) in prompt
    assert "M1, ABHA identity" in prompt
    html = page.content.decode()
    assert ">Open in Claude Code</span>" in html
    assert ">Open in Cursor</span>" in html


def test_an_agent_with_no_url_scheme_only_offers_the_command_to_copy(
    environment,
    client,
):
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    # Codex and Copilot have no scheme, so a link that could not open is not drawn.
    html = page.content.decode()
    assert "Open in Codex" not in html
    assert "Open in Copilot" not in html
    # The command is still theirs to copy.
    assert command(page, "codex") == install("codex", "abdm-m1", M1_SECTIONS)
    assert command(page, "copilot") == install("copilot", "abdm-m1", M1_SECTIONS)


def test_only_agents_with_a_scheme_build_an_install_deeplink():
    skill = a_skill("abdm-m1")
    build = ABDMAgentSkills.install_deeplink
    targets = ABDMAgentSkills.targets

    claude = build(targets["claude"], skill)
    cursor = build(targets["cursor"], skill)

    assert claude.startswith(CLAUDE_SCHEME)
    assert cursor.startswith(CURSOR_SCHEME)
    prompt = unquote(claude.split("q=", 1)[1])
    # The command rides in unrun, and the guard rides with it, so a link opened
    # against the wrong repository asks before it writes.
    assert install("claude", "abdm-m1", M1_SECTIONS) in prompt
    assert "ask me for the path" in prompt
    # An agent with no scheme builds nothing rather than a link that dies on open.
    assert build(targets["codex"], skill) is None
    assert build(targets["copilot"], skill) is None


def test_the_panel_opens_on_the_skill_for_the_milestone_being_worked_on(
    environment,
    client,
):
    approve(environment, "m1")
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    # The gateway and FHIR carry M2 as well, but M2's own skill is the one to open on.
    assert page.context["selected_skill"] == "abdm-m2"
    assert command(page, "claude") == install("claude", "abdm-m2", M1_SECTIONS)


def test_skills_for_the_milestones_on_the_product_are_recommended(environment, client):
    workspace = hie_cm_only(environment)
    client.force_login(environment["applicant"])

    page = client.get(skills_url(workspace))

    recommended = {slug for slug, row in cards(page).items() if row["recommended"]}
    assert recommended == {
        "abdm-gateway",
        "abdm-m1",
        "abdm-m2",
        "abdm-m3",
        "abdm-subscription",
        "abdm-scan-and-pay",
        "abdm-fhir",
    }
    # The track the product applied for is read first, then the shared skills it
    # was recommended, then the tracks it left out.
    codes = [group["code"] for group in page.context["skill_groups"]]
    assert codes == ["HIE-CM", "", "PHR", "HealthLocker"]


def test_skills_that_are_no_milestone_of_their_own_are_listed_apart(
    environment,
    client,
):
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    def slugs(group):
        return [row["definition"]["slug"] for row in group["skills"]]

    # HIE-CM is the four modules. The gateway is its front door, not one of them.
    assert slugs(groups(page)["HIE-CM"]) == ["abdm-m1", "abdm-m2", "abdm-m3", "abdm-m4"]
    shared = groups(page)[""]
    assert shared["title"] == "Shared Agent Skills"
    assert slugs(shared) == [
        "abdm-gateway",
        "abdm-subscription",
        "abdm-scan-and-pay",
        "abdm-fhir",
    ]
    assert 'id="agent-skills-shared-title"' in page.content.decode()


def test_a_card_says_what_the_skill_covers(environment, client):
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    rows = cards(page)
    # The title and the description are the site's own, not a copy kept here.
    assert rows["abdm-m2"]["definition"]["title"] == "M2, linking and sharing"
    # A card shows only what the opening sentence lists: its "Use when" lead-in
    # and the rest are written for an agent deciding whether to load the skill.
    assert rows["abdm-m2"]["summary"] == (
        "Care contexts, HIP initiated linking, discovery, and pushing encrypted"
        " health records to a requester."
    )
    assert rows["abdm-gateway"]["summary"] == "The gateway session and bridge registry."


def test_a_card_links_to_the_docs_the_product_form_gives_its_milestone(
    environment,
    client,
    settings,
):
    settings.ABDM_DOCS_URL = "https://docs.example"
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    links = DocsLinks()
    links.feed(page.content.decode())
    docs = "https://docs.example"
    milestones = f"{docs}/docs/hiecm/v3/milestones"
    assert links.links == {
        "Gateway, sessions and the bridge registry": (
            f"{docs}/docs/hiecm/v3/concepts/gateway"
        ),
        "M1, ABHA identity": f"{milestones}/m1",
        "M2, linking and sharing": f"{milestones}/m2",
        "M3, consent and fetching": f"{milestones}/m3",
        "M4, facility and professional registries": f"{milestones}/m4",
        "Subscriptions": f"{docs}/reference/hiecm-subscription",
        "Scan and pay": f"{docs}/reference/hiecm-scan-and-pay",
        "FHIR, generating and auditing bundles": f"{docs}/docs/hiecm/v3/concepts/fhir",
        "P1, PHR registration and login": f"{milestones}/p1",
        "P2, PHR management": f"{milestones}/p2",
        "P3, PHR subscriptions": f"{milestones}/p3",
        # P4 has no milestone page; the product form links this section too.
        "P4, health lockers": (
            f"{docs}/docs/hiecm/v3/concepts/phr#where-the-citizen-is-the-hip"
        ),
    }


def test_skills_the_product_did_not_apply_for_can_still_be_installed(
    environment,
    client,
):
    workspace = hie_cm_only(environment)
    client.force_login(environment["applicant"])

    page = client.get(skills_url(workspace))

    slugs = [skill["slug"] for skill in page.context["installable_skills"]]
    assert slugs == [
        "abdm-m1",
        "abdm-m2",
        "abdm-m3",
        "abdm-m4",
        "abdm-gateway",
        "abdm-subscription",
        "abdm-scan-and-pay",
        "abdm-fhir",
        "abdm-p1",
        "abdm-p2",
        "abdm-p3",
        "abdm-p4",
    ]
    assert not groups(page)["PHR"]["matched"]


def test_a_track_with_no_skill_of_its_own_leaves_no_empty_group(
    environment,
    client,
):
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    # NHCX and UHI carry no skills yet, so neither gets a heading with nothing under it.
    assert set(groups(page)) == {"HIE-CM", "PHR", "HealthLocker", ""}
    assert all(group["skills"] for group in page.context["skill_groups"])


def test_every_published_skill_is_named_the_way_the_source_folder_is():
    source = "plugins/abdm-integrators-assistant/skills"
    for skill in ABDMAgentSkills.skills():
        assert ABDMAgentSkills.source_url(skill) == (
            f"https://github.com/nha-in/docs/tree/main/{source}/{skill['slug']}"
        )
    assert ABDMAgentSkills.source_url().endswith(f"/tree/main/{source}")


def test_no_agent_note_is_long_enough_to_reflow_the_tabs():
    """The note shares its line with the agent tabs, so a long one wraps them."""
    for target in ABDMAgentSkills.targets.values():
        assert len(target.note) <= 48, target.label


def test_the_deployment_decides_which_documentation_site_is_installed_from(
    environment,
    client,
    settings,
):
    # A staging portal handing out production's skills would be the wrong pack.
    settings.ABDM_DOCS_URL = "https://docs.staging.example/"
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    assert "https://docs.staging.example/skills/abdm-m1/SKILL.md" in command(
        page,
        "claude",
    )
    # The trailing slash the variable was set with is not doubled up.
    assert ABDMAgentSkills.docs_url() == (
        "https://docs.staging.example/docs/hiecm/v3/getting-started/build-with-ai"
    )
    assert f'href="{ABDMAgentSkills.docs_url()}"' in page.content.decode()


def test_the_shipped_file_carries_every_skill_the_mapping_names():
    """The check that validate() cannot make: a refresh can withdraw a skill."""
    published = {skill["slug"] for skill in ABDMAgentSkills.skills()}

    assert published, "Refresh it with `manage.py fetch_agent_skills`."
    mapped = (
        set(ABDMAgentSkills.milestones_by_skill)
        | set(ABDMAgentSkills.docs_by_skill)
        | set(ABDMAgentSkills.shared_skills)
    )
    unknown = mapped - published
    assert not unknown, (
        f"{sorted(unknown)} are mapped but the site no longer "
        "publishes them. Refresh the file and settle the mapping."
    )


def test_a_newly_published_skill_is_offered_before_it_is_mapped(environment, client):
    """Naming a skill in the mapping is what recommends it for a milestone."""
    published = ABDMAgentSkills.skills()
    assert published
    mapped = dict(ABDMAgentSkills.milestones_by_skill)
    mapped.pop(published[0]["slug"])

    with mock.patch.object(ABDMAgentSkills, "milestones_by_skill", mapped):
        client.force_login(environment["applicant"])
        page = client.get(skills_url(environment["workspace"]))

    slugs = [skill["slug"] for skill in page.context["installable_skills"]]
    assert published[0]["slug"] in slugs
    assert not cards(page)[published[0]["slug"]]["recommended"]


def test_support_members_have_no_agent_skills_page(environment, client):
    workspace = environment["workspace"]
    support = UserFactory()
    Membership.objects.create(
        organisation=environment["org"],
        user=support,
        role=Role.SUPPORT,
    )
    client.force_login(support)

    assert client.get(skills_url(workspace)).status_code == 403
    overview = client.get(workspace.get_absolute_url())
    assert 'id="nav-skills"' not in main_nav(overview)


def test_other_organisations_cannot_find_the_page(environment, client):
    client.force_login(environment["outsider"])

    assert client.get(skills_url(environment["workspace"])).status_code == 404


def test_a_program_without_agent_skills_has_no_page(environment, client, monkeypatch):
    monkeypatch.setattr(ABDM, "agent_skills", None)
    workspace = environment["workspace"]
    client.force_login(environment["applicant"])

    assert client.get(skills_url(workspace)).status_code == 404
    overview = client.get(workspace.get_absolute_url())
    assert 'id="nav-skills"' not in main_nav(overview)


def test_a_skill_naming_a_milestone_outside_the_catalog_is_refused():
    with pytest.raises(ImproperlyConfigured, match="not in the catalog"):
        ABDMAgentSkills.validate({"m1": ABDM.milestones["m1"]})


def test_skills_with_nowhere_to_be_installed_from_are_refused(settings):
    # An unset variable would otherwise render a command fetching from nowhere.
    settings.ABDM_DOCS_URL = ""

    with pytest.raises(ImproperlyConfigured, match="ABDM_DOCS_URL"):
        ABDMAgentSkills.validate(ABDM.milestones)


def test_a_skill_the_file_no_longer_lists_is_not_offered(environment, client):
    """A withdrawn skill leaves the page, rather than an install that 404s."""
    kept = [skill for skill in ABDMAgentSkills.skills() if skill["slug"] != "abdm-m1"]

    with mock.patch.object(ABDMAgentSkills, "skills", classmethod(lambda cls: kept)):
        client.force_login(environment["applicant"])
        page = client.get(skills_url(environment["workspace"]))

    assert "abdm-m1" not in cards(page)
    assert "abdm-m2" in cards(page)
