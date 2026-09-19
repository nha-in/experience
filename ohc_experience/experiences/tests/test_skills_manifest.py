"""What the portal does with the file the Agent Skills are taken into."""

import json

import pytest
from django.core.exceptions import ImproperlyConfigured

from ohc_experience.experiences import skills_manifest

SKILL = {
    "slug": "abdm-m1",
    "title": "M1, ABHA identity",
    "description": "Use when building M1.",
    "sections": ["scaffold", "integrate"],
}


@pytest.fixture(autouse=True)
def _forget_files():
    """The reader caches by path, and tmp_path reuses names between tests."""
    skills_manifest.read.cache_clear()


def write(tmp_path, document):
    path = tmp_path / "skills.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_the_skills_are_read_as_the_file_lists_them(tmp_path):
    path = write(
        tmp_path,
        {
            "skills": [SKILL, {**SKILL, "slug": "abdm-m2", "title": "M2"}],
        },
    )

    skills = skills_manifest.read(path)

    assert [skill["slug"] for skill in skills] == ["abdm-m1", "abdm-m2"]
    assert skills[0] == SKILL


def test_a_field_the_command_adds_arrives_without_being_declared(tmp_path):
    path = write(tmp_path, {"skills": [{**SKILL, "example": "curl ..."}]})

    assert skills_manifest.read(path)[0]["example"] == "curl ..."


def test_the_file_is_read_once_however_often_it_is_asked_for(tmp_path):
    path = write(tmp_path, {"skills": [SKILL]})

    first = skills_manifest.read(path)
    path.write_text(json.dumps({"skills": [{**SKILL, "slug": "other"}]}), "utf-8")

    assert skills_manifest.read(path) is first


def test_a_file_that_is_not_there_yet_reads_as_no_skills(tmp_path):
    # Otherwise the command that writes it could not boot far enough to run.
    assert skills_manifest.read(tmp_path / "missing.json") == ()


@pytest.mark.parametrize(
    ("document", "complaint"),
    [
        ({"skills": []}, "lists no skills"),
        ({}, "skills"),
        ({"skills": [{"title": "No folder"}]}, "slug"),
        ({"skills": "not a list"}, "cannot be used"),
        ({"skills": [SKILL, SKILL]}, "lists one skill twice"),
    ],
)
def test_a_file_that_cannot_be_used_says_so(tmp_path, document, complaint):
    path = write(tmp_path, document)

    with pytest.raises(ImproperlyConfigured, match=complaint):
        skills_manifest.read(path)


def test_a_file_that_is_not_json_names_the_command_that_rewrites_it(tmp_path):
    path = tmp_path / "skills.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(ImproperlyConfigured, match="fetch_agent_skills"):
        skills_manifest.read(path)
