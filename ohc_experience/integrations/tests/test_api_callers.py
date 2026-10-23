"""API keys for outside teams: printed once when added, only their hash stored."""

from __future__ import annotations

import re
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from ohc_experience.integrations.models import KEY_PREFIX
from ohc_experience.integrations.models import ApiCaller

pytestmark = pytest.mark.django_db


def run(*args) -> str:
    out = StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


def printed_key(printed: str) -> str:
    return re.search(rf"{KEY_PREFIX}\S+", printed).group()


def test_adding_a_caller_prints_a_key_that_opens():
    printed = run("add_api_caller", "HIE-CM team")

    caller = ApiCaller.objects.get()
    assert caller.name == "HIE-CM team"
    assert f"API caller {caller.pk}" in printed
    assert ApiCaller.for_key(printed_key(printed)) == caller


def test_only_the_hash_of_the_key_is_stored():
    key = printed_key(run("add_api_caller", "HIE-CM team"))

    assert key not in ApiCaller.objects.get().key_hash


def test_each_caller_gets_a_key_of_its_own():
    first = printed_key(run("add_api_caller", "HIE-CM team"))
    second = printed_key(run("add_api_caller", "PHR team"))

    assert first != second
    assert ApiCaller.for_key(second).name == "PHR team"


def test_a_deactivated_caller_no_longer_opens():
    key = printed_key(run("add_api_caller", "HIE-CM team"))
    caller = ApiCaller.objects.get()

    printed = run("deactivate_api_caller", str(caller.pk))

    assert ApiCaller.for_key(key) is None
    assert "HIE-CM team" in printed


def test_deactivating_an_unknown_caller_says_so():
    with pytest.raises(CommandError, match="No API caller 404"):
        run("deactivate_api_caller", "404")
