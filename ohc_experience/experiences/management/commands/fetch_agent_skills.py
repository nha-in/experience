"""Take the published Agent Skills into the file the portal reads.

    python manage.py fetch_agent_skills

Reads the site this deployment is pointed at, or --url. --check writes nothing
and fails when the file is behind.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from ohc_experience.experiences.registry import get_program

TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 1024 * 1024
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "*/*"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.read(MAX_RESPONSE_BYTES)
    except (urllib.error.URLError, OSError) as error:
        msg = f"Could not read {url}: {error}"
        raise CommandError(msg) from error


def _description(body: str) -> str:
    """The `description` a SKILL.md declares, quoted or folded over lines."""
    block = FRONTMATTER.match(body)
    if not block:
        return ""
    collected: list[str] = []
    for line in block.group(1).splitlines():
        key = re.match(r"([A-Za-z][\w-]*):\s*(.*)\Z", line)
        if key and key.group(1) == "description":
            collected = [key.group(2)]
        elif key:
            if collected:
                break
        elif collected:
            collected.append(line.strip())
    value = " ".join(part for part in collected if part).strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.strip()


def _sections(files) -> list[str]:
    """The reference files beside SKILL.md, named as the command loops over them."""
    if not isinstance(files, list):
        return []
    return [
        path.removeprefix("references/").removesuffix(".md")
        for path in files
        if isinstance(path, str) and path.startswith("references/")
    ]


class Command(BaseCommand):
    help = "Refresh the published Agent Skills this portal lists."

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            default="",
            help="The documentation site to read, instead of this deployment's.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Write nothing; fail when the file is behind the site.",
        )

    def handle(self, *args, **options):
        catalogue = get_program().agent_skills
        if catalogue is None:
            msg = "This program publishes no Agent Skills."
            raise CommandError(msg)
        path = catalogue.manifest_path
        if path is None:
            msg = f"{catalogue.__name__} names no file to write the skills into."
            raise CommandError(msg)
        base = (options["url"] or catalogue.base_url()).rstrip("/")
        if not base:
            msg = (
                "No documentation site to read. Set "
                f"{catalogue.base_url_setting or 'the site setting'} or pass --url."
            )
            raise CommandError(msg)
        document = self._fetch(f"{base}{catalogue.skills_path}")
        self._write(Path(path), document, check=options["check"])

    def _fetch(self, skills_url: str) -> dict:
        index_url = f"{skills_url}/index.json"
        self.stdout.write(f"Reading {index_url}")
        try:
            entries = json.loads(_get(index_url))["skills"]
        except (ValueError, KeyError, TypeError) as error:
            msg = f"{index_url} does not list skills: {error}"
            raise CommandError(msg) from error
        skills = []
        for entry in entries:
            slug = entry.get("name")
            if not slug:
                msg = f"{index_url} lists a skill with no name."
                raise CommandError(msg)
            body = _get(f"{skills_url}/{slug}/SKILL.md").decode("utf-8", "replace")
            description = _description(body)
            if not description:
                self.stdout.write(
                    self.style.WARNING(f"{slug} declares no description."),
                )
            skills.append(
                {
                    "slug": slug,
                    "title": entry.get("title") or slug,
                    "description": description,
                    "sections": _sections(entry.get("files")),
                },
            )
            self.stdout.write(f"  {slug}")
        return {"skills": skills}

    def _write(self, path: Path, document: dict, *, check: bool):
        body = f"{json.dumps(document, indent=2, ensure_ascii=False)}\n"
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current == body:
            self.stdout.write(self.style.SUCCESS(f"{path.name} is up to date."))
            return
        if check:
            msg = (
                f"{path} is behind the documentation site. Refresh it with "
                "`manage.py fetch_agent_skills` and commit the result."
            )
            raise CommandError(msg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {len(document['skills'])} skill(s) into {path}.",
            ),
        )
