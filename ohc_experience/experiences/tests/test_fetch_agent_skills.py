"""Taking the published Agent Skills into the file the portal reads."""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from ohc_experience.abdm.skills import ABDMAgentSkills
from ohc_experience.experiences.management.commands import fetch_agent_skills

SITE = ABDMAgentSkills.base_url()
INDEX = {
    "catalogue_version": "2026.08.24",
    "skills": [
        {
            "name": "abdm-m1",
            "title": "M1, ABHA identity",
            "files": ["SKILL.md", "references/scaffold.md", "references/test.md"],
        },
    ],
}
SKILL_MD = (
    "---\n"
    "name: abdm-m1\n"
    "description: Use when building M1. Carries the endpoints and the codes.\n"
    "---\n\n"
    "# ABDM M1\n"
)


@pytest.fixture
def site(monkeypatch):
    """The documentation site, answering with whatever the test puts in it."""
    pages = {
        f"{SITE}/skills/index.json": json.dumps(INDEX).encode(),
        f"{SITE}/skills/abdm-m1/SKILL.md": SKILL_MD.encode(),
    }
    monkeypatch.setattr(fetch_agent_skills, "_get", lambda url: pages[url])
    return pages


@pytest.fixture
def written(tmp_path, monkeypatch):
    path = tmp_path / "skills.json"
    monkeypatch.setattr(ABDMAgentSkills, "manifest_path", path)
    return path


def run(**options):
    out = StringIO()
    call_command("fetch_agent_skills", stdout=out, **options)
    return out.getvalue()


def test_it_writes_what_the_site_publishes(site, written):
    output = run()

    document = json.loads(written.read_text(encoding="utf-8"))
    assert document["skills"] == [
        {
            "slug": "abdm-m1",
            "title": "M1, ABHA identity",
            "description": (
                "Use when building M1. Carries the endpoints and the codes."
            ),
            "sections": ["scaffold", "test"],
        },
    ]
    assert document.keys() == {"skills"}
    assert "Wrote 1 skill(s) into" in output


def test_the_file_it_writes_is_the_file_the_portal_reads(site, written):
    run()

    assert [skill["slug"] for skill in ABDMAgentSkills.skills()] == ["abdm-m1"]


def test_writing_it_twice_changes_nothing_the_second_time(site, written):
    run()
    first = written.read_text(encoding="utf-8")

    assert "is up to date" in run()
    assert written.read_text(encoding="utf-8") == first
    assert first.endswith("\n")


def test_check_refuses_when_the_file_is_behind(site, written):
    written.write_text(json.dumps({"skills": []}), encoding="utf-8")

    with pytest.raises(CommandError, match="fetch_agent_skills"):
        run(check=True)

    # It reports, it does not repair.
    assert json.loads(written.read_text(encoding="utf-8")) == {"skills": []}


def test_check_is_quiet_when_the_file_is_current(site, written):
    run()

    assert "is up to date" in run(check=True)


def test_a_skill_that_says_nothing_about_itself_is_called_out(site, written):
    site[f"{SITE}/skills/abdm-m1/SKILL.md"] = b"---\nname: abdm-m1\n---\n"

    output = run()

    assert "declares no description" in output
    assert (
        json.loads(written.read_text(encoding="utf-8"))["skills"][0]["description"]
        == ""
    )


def test_another_site_can_be_named(monkeypatch, written):
    pages = {
        "https://staging.example/skills/index.json": json.dumps(INDEX).encode(),
        "https://staging.example/skills/abdm-m1/SKILL.md": SKILL_MD.encode(),
    }
    monkeypatch.setattr(fetch_agent_skills, "_get", lambda url: pages[url])

    run(url="https://staging.example/")

    document = json.loads(written.read_text(encoding="utf-8"))
    assert [skill["slug"] for skill in document["skills"]] == ["abdm-m1"]


@pytest.mark.parametrize(
    "frontmatter",
    [
        'description: "Quoted, and the quotes come off."\n',
        "description: Folded over\n  a second line.\n",
        "name: abdm-m1\ndescription: After another key.\n",
    ],
)
def test_the_description_is_read_however_the_generator_wrote_it(frontmatter):
    body = f"---\n{frontmatter}---\n\n# Heading\n"

    value = fetch_agent_skills._description(body)  # noqa: SLF001

    assert value
    assert not value.startswith('"')
    assert "\n" not in value


def test_a_file_with_no_frontmatter_describes_nothing():
    assert fetch_agent_skills._description("# Heading\n") == ""  # noqa: SLF001
