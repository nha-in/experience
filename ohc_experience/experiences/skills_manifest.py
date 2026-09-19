"""The Agent Skills `manage.py fetch_agent_skills` last took from the site."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured

if TYPE_CHECKING:
    from pathlib import Path


def _broken(path: Path, reason) -> ImproperlyConfigured:
    return ImproperlyConfigured(
        f"{path} cannot be used ({reason}). Rewrite it with "
        "`manage.py fetch_agent_skills`.",
    )


@lru_cache(maxsize=4)
def read(path: Path) -> tuple[dict, ...]:
    """Whatever the file lists, or nothing until the command has written it."""
    if not path.exists():
        return ()
    try:
        skills = json.loads(path.read_text(encoding="utf-8"))["skills"]
        slugs = [skill["slug"] for skill in skills]
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise _broken(path, error) from error
    if not slugs:
        raise _broken(path, "it lists no skills")
    if len(slugs) != len(set(slugs)):
        raise _broken(path, "it lists one skill twice")
    return tuple(skills)
