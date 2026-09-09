"""The definition contract, checked without a database."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from ohc_experience.experiences import permission_keys
from ohc_experience.experiences.definitions import ApplicationDefinition
from ohc_experience.experiences.definitions import ExperienceContext
from ohc_experience.experiences.definitions import RoleDefinition
from ohc_experience.experiences.models import SubmissionStatus
from ohc_experience.experiences.registry import ExperienceRegistry
from ohc_experience.experiences.registry import registry
from ohc_experience.experiences.tests.sample import Certification
from ohc_experience.experiences.tests.sample import LockerOperations
from ohc_experience.experiences.tests.sample import Profile
from ohc_experience.experiences.tests.sample import SampleExperience
from ohc_experience.experiences.tests.sample import Scope

FORM_COUNT = 7
BASE_REQUIRED_FORM_COUNT = 6


def _definition(**overrides):
    attrs = {
        "key": "broken",
        "name": "Broken",
        "description": "",
        "statuses": SampleExperience.statuses,
        "permissions": SampleExperience.permissions,
        "roles": SampleExperience.roles,
        "forms": SampleExperience.forms,
        "actions": SampleExperience.actions,
        **overrides,
    }
    return type("Broken", (ApplicationDefinition,), attrs)


def _submission(data=None, *, valid_until=None):
    return SimpleNamespace(
        status=SubmissionStatus.COMPLETED,
        data=data or {},
        valid_until=valid_until,
        is_expired=bool(valid_until and valid_until < timezone.localdate()),
    )


def _context(*, status="draft", permissions=(), submissions=None):
    application = SimpleNamespace(
        status=status,
        application_type=SampleExperience.key,
    )
    return ExperienceContext(
        application=application,
        user=None,
        permissions=frozenset(permissions),
        submissions=submissions or {},
        submission_history={},
    )


def test_registry_holds_the_sample_and_rejects_duplicates_and_unknown_keys():
    assert registry.get(SampleExperience.key) is SampleExperience
    assert SampleExperience in registry.all()

    fresh = ExperienceRegistry()
    fresh.register(SampleExperience)
    with pytest.raises(ImproperlyConfigured, match="already registered"):
        fresh.register(SampleExperience)
    with pytest.raises(ImproperlyConfigured, match="Unknown experience"):
        fresh.get("nope")


def test_definition_validation_catches_the_common_mistakes():
    with pytest.raises(ImproperlyConfigured, match="duplicate form keys"):
        _definition(forms=(Profile, Profile)).validate()
    with pytest.raises(ImproperlyConfigured, match="unknown permissions"):
        _definition(
            roles=(
                RoleDefinition(
                    "applicant_owner",
                    "",
                    "",
                    "organisation",
                    frozenset({"nope"}),
                ),
            ),
        ).validate()
    with pytest.raises(ImproperlyConfigured, match="unknown dependencies"):
        _definition(forms=(Scope,)).validate()
    with pytest.raises(ImproperlyConfigured, match="no owner role"):
        _definition(owner_role_key="nobody").validate()
    _definition().validate()


def test_sample_exposes_forms_roles_permissions_and_actions():
    assert len(SampleExperience.forms) == FORM_COUNT
    assert SampleExperience.get_form("nope") is None
    assert SampleExperience.get_status("nope") is SampleExperience.statuses[0]
    assert SampleExperience.get_role("decision_maker").audience == "platform"
    assert {action.key for action in SampleExperience.actions} == {
        "submit",
        "ask_review_team",
        "start_review",
        "raise_query",
        "approve",
        "reject",
    }
    assert permission_keys.WITHDRAW_APPLICATION not in {
        permission.key for permission in SampleExperience.permissions
    }


def test_form_availability_explains_why_a_form_is_closed():
    editable = _context(permissions={permission_keys.EDIT_FORMS})

    assert Profile.availability(editable) == (True, "")
    _available, no_permission = Profile.availability(_context())
    assert "cannot edit forms" in str(no_permission)
    _available, wrong_status = Profile.availability(
        _context(status="approved", permissions={permission_keys.EDIT_FORMS}),
    )
    assert "cannot be changed" in str(wrong_status)
    _available, gated = Scope.availability(editable)
    assert "preceding forms" in str(gated)


def test_applicability_and_visibility_follow_earlier_answers():
    without_scope = _context()
    with_locker = _context(
        submissions={
            Profile.key: _submission(),
            Scope.key: _submission({"channels": ["web", "locker"]}),
        },
    )
    without_locker = _context(
        submissions={
            Profile.key: _submission(),
            Scope.key: _submission({"channels": ["web"]}),
        },
    )

    assert LockerOperations.is_applicable(without_scope) is False
    assert LockerOperations.is_visible(without_scope) is False
    assert LockerOperations.is_applicable(with_locker) is True
    assert LockerOperations.is_visible(with_locker) is True
    assert LockerOperations.is_applicable(without_locker) is False
    assert Scope.is_visible(without_scope) is False
    assert Scope.is_visible(with_locker) is True


def test_renewal_is_due_inside_the_window_only():
    soon = _context(
        submissions={
            Certification.key: _submission(
                valid_until=timezone.localdate() + timedelta(days=10),
            ),
        },
    )
    later = _context(
        submissions={
            Certification.key: _submission(
                valid_until=timezone.localdate() + timedelta(days=100),
            ),
        },
    )
    expired = _context(
        submissions={
            Certification.key: _submission(
                valid_until=timezone.localdate() - timedelta(days=1),
            ),
        },
    )

    assert Certification.is_renewal_due(soon) is True
    assert Certification.is_renewal_due(later) is False
    assert Certification.is_complete(soon) is True
    assert Certification.is_complete(expired) is False


def test_progress_counts_only_forms_the_current_answers_require():
    empty = SampleExperience.calculate_progress(_context())
    complete = SampleExperience.calculate_progress(
        _context(
            submissions={
                Profile.key: _submission(),
                Scope.key: _submission({"channels": ["web"]}),
                "readiness": _submission(),
                "compliance": _submission(),
                Certification.key: _submission(
                    valid_until=timezone.localdate() + timedelta(days=300),
                ),
                "declaration": _submission(),
            },
        ),
    )

    assert (empty.completed, empty.total, empty.percentage) == (
        0,
        BASE_REQUIRED_FORM_COUNT,
        0,
    )
    assert empty.next_form_key == Profile.key
    assert (complete.completed, complete.total, complete.percentage) == (
        BASE_REQUIRED_FORM_COUNT,
        BASE_REQUIRED_FORM_COUNT,
        100,
    )
    assert complete.next_form_key == ""
