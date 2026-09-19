# ruff: noqa: F811, PLR2004
from unittest import mock

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

PUBLISHED = "https://abdm-docs.dev.eka.care/skills"


def skills_url(workspace):
    return reverse("experiences:agent-skills", args=[workspace.reference])


def command(response, target):
    """One agent's install command, as the copy button reads it."""
    parser = CommandText(f"agent-skill-command-{target}", with_hidden=False)
    parser.feed(response.content.decode())
    return " ".join("".join(parser.parts).split())


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
    """A product that applied for one track, so the other tracks stay locked."""
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


M1_SECTIONS = ("scaffold", "integrate", "debug", "test")


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


def test_the_command_fetches_only_the_sections_the_chosen_skill_has(
    environment,
    client,
):
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    # FHIR carries two sections rather than the usual four, and the command has
    # to name its own, or the loop fetches files that are not published.
    html = page.content.decode()
    assert (
        install("claude", "abdm-fhir", ("generate", "audit"))
        .split(" && for f in ")[1]
        .startswith("generate audit;")
    )
    assert ">generate audit</span>" in html
    assert ">scaffold integrate debug test</span>" in html


def test_the_panel_opens_on_the_skill_for_the_milestone_being_worked_on(
    environment,
    client,
):
    approve(environment, "m1")
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    assert page.context["selected_skill"] == "abdm-m2"
    rows = cards(page)
    # Nothing tells the portal a skill was installed, so an approved milestone
    # is not claimed as one being used.
    assert rows["abdm-m1"]["badge"] is None
    assert rows["abdm-m2"]["badge"] == ("Recommended", "primary")
    # Nothing is claimed about a milestone the product has not reached either.
    assert rows["abdm-m3"]["badge"] is None
    assert command(page, "claude") == install("claude", "abdm-m2", M1_SECTIONS)


def test_the_first_milestone_is_recommended_until_it_is_approved(environment, client):
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    assert page.context["selected_skill"] == "abdm-m1"
    assert cards(page)["abdm-m1"]["badge"] == ("Recommended", "primary")
    # FHIR is carried by M2, so it waits its turn rather than crowding the first.
    assert cards(page)["abdm-fhir"]["badge"] is None
    assert cards(page)["abdm-m2"]["badge"] is None


def test_a_card_says_what_it_carries_and_what_it_does(environment, client):
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    rows = cards(page)
    # What opening the folder gets you, which is what tells two skills apart
    # now that each one covers a whole module.
    assert rows["abdm-m2"]["kicker"] == "Scaffold · Integrate · Debug · Test"
    assert rows["abdm-fhir"]["kicker"] == "Generate · Audit"
    assert rows["abdm-phr-services"]["kicker"] == "Integrate · Debug · Test"
    # The title and the description are the site's own, not a copy kept here.
    assert rows["abdm-m2"]["definition"]["title"] == "M2, linking and sharing"
    # A card shows the opening sentence: the rest is written for an agent
    # deciding whether to load the skill, and does not fit a card.
    summary = rows["abdm-m2"]["summary"]
    assert summary.startswith(
        "Use when building, debugging or testing ABDM Milestone 2",
    )
    assert summary.endswith(".")
    assert len(summary) < len(rows["abdm-m2"]["definition"]["description"])


def test_skills_for_a_track_the_product_left_out_are_locked(environment, client):
    workspace = hie_cm_only(environment)
    client.force_login(environment["applicant"])

    page = client.get(skills_url(workspace))

    hie_cm, phr = groups(page)["HIE-CM"], groups(page)["PHR"]
    assert hie_cm["matched"]
    assert not phr["matched"]
    # The matched track comes first, so what can be installed now is read first.
    codes = [group["code"] for group in page.context["skill_groups"]]
    assert codes == ["HIE-CM", "PHR"]
    assert cards(page)["abdm-p1"]["locked"]
    assert cards(page)["abdm-p1"]["needs"] == "PHR1"
    assert cards(page)["abdm-p3"]["needs"] == "PHR1 and HL1"
    # M4 sits in a track this product did have, but it left the milestone out.
    assert cards(page)["abdm-m4"]["locked"]
    assert cards(page)["abdm-m4"]["needs"] == "M4"
    assert not cards(page)["abdm-m1"]["locked"]
    # A locked skill is never offered to the command, which names only one.
    slugs = [skill["slug"] for skill in page.context["installable_skills"]]
    assert slugs == ["abdm-m1", "abdm-m2", "abdm-m3", "abdm-fhir"]
    html = page.content.decode()
    assert "Locked until PHR is added to this product" in html
    edit_url = reverse("experiences:product-edit", args=[workspace.reference])
    assert f'href="{edit_url}"' in html


def test_a_track_with_no_skill_of_its_own_leaves_no_empty_group(
    environment,
    client,
):
    client.force_login(environment["applicant"])

    page = client.get(skills_url(environment["workspace"]))

    # NHCX and UHI carry no skills yet, so neither gets a heading with nothing under it.
    assert set(groups(page)) == {"HIE-CM", "PHR"}
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
    unknown = set(ABDMAgentSkills.milestones_by_skill) - published
    assert not unknown, (
        f"{sorted(unknown)} are given milestones but the site no longer "
        "publishes them. Refresh the file and settle the mapping."
    )


def test_a_newly_published_skill_is_offered_before_it_is_mapped(environment, client):
    """Naming a skill in the mapping is what locks it behind a milestone."""
    published = ABDMAgentSkills.skills()
    assert published
    mapped = dict(ABDMAgentSkills.milestones_by_skill)
    mapped.pop(published[0]["slug"])

    with mock.patch.object(ABDMAgentSkills, "milestones_by_skill", mapped):
        client.force_login(environment["applicant"])
        page = client.get(skills_url(environment["workspace"]))

    row = cards(page)[published[0]["slug"]]
    assert not row["locked"]
    assert row["needs"] == ""


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
    kept = ABDMAgentSkills.skills()[1:]

    with mock.patch.object(ABDMAgentSkills, "skills", classmethod(lambda cls: kept)):
        client.force_login(environment["applicant"])
        page = client.get(skills_url(environment["workspace"]))

    assert "abdm-m1" not in cards(page)
    assert "abdm-m2" in cards(page)
