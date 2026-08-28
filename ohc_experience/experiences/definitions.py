from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _

from . import permission_keys
from .models import SubmissionStatus

if TYPE_CHECKING:
    from datetime import date

    from django import forms
    from django.contrib.auth.base_user import AbstractBaseUser

    from .models import ApplicationFormSubmission
    from .models import ApplicationInstance


@dataclass(frozen=True)
class PermissionDefinition:
    key: str
    label: str
    description: str
    category: str


@dataclass(frozen=True)
class RoleDefinition:
    key: str
    label: str
    description: str
    audience: str
    permissions: frozenset[str]


@dataclass(frozen=True)
class StatusDefinition:
    key: str
    label: str
    description: str
    variant: str = "muted"
    terminal: bool = False


@dataclass(frozen=True)
class QueryRequest:
    subject: str
    message: str
    form_key: str = ""
    due_at: date | None = None
    initial_status: str = "awaiting_applicant"


@dataclass(frozen=True)
class ActionResult:
    message: str
    new_status: str = ""
    metadata_updates: dict[str, Any] = field(default_factory=dict)
    outcome_updates: dict[str, Any] = field(default_factory=dict)
    query: QueryRequest | None = None


@dataclass(frozen=True)
class FormActionResult:
    message: str
    metadata_updates: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApplicationProgress:
    completed: int
    total: int
    percentage: int
    next_form_key: str = ""


@dataclass
class ExperienceContext:
    application: ApplicationInstance
    user: AbstractBaseUser
    permissions: frozenset[str]
    submissions: dict[str, ApplicationFormSubmission]

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def has_completed(self, form_key: str) -> bool:
        submission = self.submissions.get(form_key)
        return bool(
            submission and submission.status == SubmissionStatus.COMPLETED,
        )

    def form_data(self, form_key: str) -> dict[str, Any]:
        submission = self.submissions.get(form_key)
        return dict(submission.data) if submission else {}


@dataclass(frozen=True)
class FormActionState:
    definition: type[ApplicationFormAction]
    available: bool
    reason: str
    outcome_label: str = ""


@dataclass(frozen=True)
class FormState:
    definition: type[ApplicationFormDefinition]
    submission: ApplicationFormSubmission | None
    applicable: bool
    visible: bool
    can_submit: bool
    reason: str
    action_label: str
    actions: tuple[FormActionState, ...] = ()

    @property
    def is_completed(self) -> bool:
        return bool(
            self.submission and self.submission.status == SubmissionStatus.COMPLETED,
        )


@dataclass(frozen=True)
class ActionState:
    definition: type[ApplicationAction]
    available: bool
    reason: str


class ApplicationFormDefinition:
    key: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    form_class: ClassVar[type[forms.Form]]
    dependencies: ClassVar[tuple[str, ...]] = ()
    schema_version: ClassVar[int] = 1
    required: ClassVar[bool] = True
    allow_updates: ClassVar[bool] = False
    permission: ClassVar[str] = permission_keys.EDIT_FORMS
    actions: ClassVar[tuple[type[ApplicationFormAction], ...]] = ()
    editable_statuses: ClassVar[frozenset[str]] = frozenset(
        {"draft", "changes_requested"},
    )

    @classmethod
    def is_applicable(cls, context: ExperienceContext) -> bool:
        return True

    @classmethod
    def is_visible(cls, context: ExperienceContext) -> bool:
        return cls.is_applicable(context) and all(
            context.has_completed(key) for key in cls.dependencies
        )

    @classmethod
    def availability(cls, context: ExperienceContext) -> tuple[bool, str]:
        if not cls.is_applicable(context):
            return False, _(
                "This form is not required for the current application scope.",
            )
        if not cls.is_visible(context):
            return False, _("Complete the preceding forms first.")
        if not context.has_permission(cls.permission):
            return False, _("Your application role cannot edit forms.")
        if context.application.status not in cls.editable_statuses:
            return False, _("Forms cannot be changed in the current status.")
        submission = context.submissions.get(cls.key)
        if (
            submission
            and submission.status == SubmissionStatus.COMPLETED
            and not cls.allow_updates
        ):
            return False, _("This form is locked after it is completed.")
        return True, ""

    @classmethod
    def get_action(cls, key: str) -> type[ApplicationFormAction] | None:
        return next((item for item in cls.actions if item.key == key), None)

    @classmethod
    def action_states(cls, context: ExperienceContext) -> list[FormActionState]:
        submission = context.submissions.get(cls.key)
        return [
            FormActionState(
                definition=action,
                available=available,
                reason=reason,
                outcome_label=action.outcome_label(context, submission),
            )
            for action in cls.actions
            for available, reason in [action.availability(context, submission)]
        ]

    @classmethod
    def get_initial(cls, context: ExperienceContext) -> dict[str, Any]:
        submission = context.submissions.get(cls.key)
        return dict(submission.data) if submission else {}

    @classmethod
    def metadata_updates(
        cls,
        cleaned_data: dict[str, Any],
        context: ExperienceContext,
    ) -> dict[str, Any]:
        return {}

    @classmethod
    def build_form(
        cls,
        *,
        context: ExperienceContext,
        data=None,
        files=None,
    ) -> forms.Form:
        submission = context.submissions.get(cls.key)
        existing_files = {
            attachment.field_key: attachment
            for attachment in (
                submission.attachments.filter(is_current=True) if submission else []
            )
        }
        kwargs: dict[str, Any] = {
            "experience_context": context,
            "existing_files": existing_files,
        }
        if data is None:
            kwargs["initial"] = cls.get_initial(context)
        else:
            kwargs["data"] = data
            kwargs["files"] = files
        return cls.form_class(**kwargs)


class ApplicationFormAction:
    key: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    permission: ClassVar[str]
    allowed_statuses: ClassVar[frozenset[str]] = frozenset()
    form_class: ClassVar[type[forms.Form] | None] = None
    style: ClassVar[str] = "default"

    @classmethod
    def availability(
        cls,
        context: ExperienceContext,
        submission: ApplicationFormSubmission | None,
    ) -> tuple[bool, str]:
        if not context.has_permission(cls.permission):
            return False, _("Your application role does not include this permission.")
        if submission is None or submission.status != SubmissionStatus.COMPLETED:
            return False, _("Complete the form before using this action.")
        if (
            cls.allowed_statuses
            and context.application.status not in cls.allowed_statuses
        ):
            return False, _("This action is not available in the current status.")
        return cls.extra_availability(context, submission)

    @classmethod
    def extra_availability(
        cls,
        context: ExperienceContext,
        submission: ApplicationFormSubmission,
    ) -> tuple[bool, str]:
        return True, ""

    @classmethod
    def outcome_label(
        cls,
        context: ExperienceContext,
        submission: ApplicationFormSubmission | None,
    ) -> str:
        return ""

    @classmethod
    def build_form(
        cls,
        *,
        context: ExperienceContext,
        submission: ApplicationFormSubmission,
        data=None,
    ) -> forms.Form | None:
        if cls.form_class is None:
            return None
        return cls.form_class(data=data, experience_context=context)

    @classmethod
    def perform(
        cls,
        context: ExperienceContext,
        submission: ApplicationFormSubmission,
        cleaned_data: dict[str, Any],
    ) -> FormActionResult:
        raise NotImplementedError


class ApplicationAction:
    key: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    permission: ClassVar[str]
    allowed_statuses: ClassVar[frozenset[str]] = frozenset()
    form_class: ClassVar[type[forms.Form] | None] = None
    style: ClassVar[str] = "default"

    @classmethod
    def availability(cls, context: ExperienceContext) -> tuple[bool, str]:
        if not context.has_permission(cls.permission):
            return False, _("Your application role does not include this permission.")
        if (
            cls.allowed_statuses
            and context.application.status not in cls.allowed_statuses
        ):
            return False, _("This action is not available in the current status.")
        return cls.extra_availability(context)

    @classmethod
    def extra_availability(
        cls,
        context: ExperienceContext,
    ) -> tuple[bool, str]:
        return True, ""

    @classmethod
    def build_form(cls, *, context: ExperienceContext, data=None) -> forms.Form | None:
        if cls.form_class is None:
            return None
        return cls.form_class(data=data, experience_context=context)

    @classmethod
    def perform(
        cls,
        context: ExperienceContext,
        cleaned_data: dict[str, Any],
    ) -> ActionResult:
        raise NotImplementedError


class ApplicationDefinition:
    key: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    reference_prefix: ClassVar[str] = "APP"
    initial_status: ClassVar[str] = "draft"
    owner_role_key: ClassVar[str] = "applicant_owner"
    statuses: ClassVar[tuple[StatusDefinition, ...]]
    permissions: ClassVar[tuple[PermissionDefinition, ...]]
    roles: ClassVar[tuple[RoleDefinition, ...]]
    forms: ClassVar[tuple[type[ApplicationFormDefinition], ...]]
    actions: ClassVar[tuple[type[ApplicationAction], ...]]

    @classmethod
    def validate(cls) -> None:
        collections = {
            "status": [item.key for item in cls.statuses],
            "permission": [item.key for item in cls.permissions],
            "role": [item.key for item in cls.roles],
            "form": [item.key for item in cls.forms],
            "action": [item.key for item in cls.actions],
        }
        for label, keys in collections.items():
            if len(keys) != len(set(keys)):
                msg = f"{cls.key} has duplicate {label} keys."
                raise ImproperlyConfigured(msg)
        permission_keys = set(collections["permission"])
        for role in cls.roles:
            unknown = role.permissions - permission_keys
            if unknown:
                msg = f"{cls.key}.{role.key} has unknown permissions: {unknown}"
                raise ImproperlyConfigured(msg)
        for action in cls.actions:
            if action.permission not in permission_keys:
                msg = f"{cls.key}.{action.key} has an unknown permission."
                raise ImproperlyConfigured(msg)
        if cls.owner_role_key not in collections["role"]:
            msg = f"{cls.key} has no owner role named {cls.owner_role_key}."
            raise ImproperlyConfigured(msg)
        if cls.initial_status not in collections["status"]:
            msg = f"{cls.key} has no initial status named {cls.initial_status}."
            raise ImproperlyConfigured(msg)
        cls._validate_forms(set(collections["form"]), permission_keys)

    @classmethod
    def _validate_forms(
        cls,
        form_keys: set[str],
        permission_keys: set[str],
    ) -> None:
        for form_definition in cls.forms:
            unknown_dependencies = set(form_definition.dependencies) - form_keys
            if unknown_dependencies:
                msg = (
                    f"{cls.key}.{form_definition.key} has unknown dependencies: "
                    f"{unknown_dependencies}"
                )
                raise ImproperlyConfigured(msg)
            action_keys = [action.key for action in form_definition.actions]
            if len(action_keys) != len(set(action_keys)):
                msg = f"{cls.key}.{form_definition.key} has duplicate action keys."
                raise ImproperlyConfigured(msg)
            if form_definition.permission not in permission_keys:
                msg = f"{cls.key}.{form_definition.key} has an unknown permission."
                raise ImproperlyConfigured(msg)
            for action in form_definition.actions:
                if action.permission not in permission_keys:
                    msg = (
                        f"{cls.key}.{form_definition.key}.{action.key} has an "
                        "unknown permission."
                    )
                    raise ImproperlyConfigured(msg)

    @classmethod
    def get_status(cls, key: str) -> StatusDefinition:
        return next((item for item in cls.statuses if item.key == key), cls.statuses[0])

    @classmethod
    def get_permission(cls, key: str) -> PermissionDefinition | None:
        return next((item for item in cls.permissions if item.key == key), None)

    @classmethod
    def get_role(cls, key: str) -> RoleDefinition | None:
        return next((item for item in cls.roles if item.key == key), None)

    @classmethod
    def get_form(cls, key: str) -> type[ApplicationFormDefinition] | None:
        return next((item for item in cls.forms if item.key == key), None)

    @classmethod
    def get_action(cls, key: str) -> type[ApplicationAction] | None:
        return next((item for item in cls.actions if item.key == key), None)

    @classmethod
    def form_states(cls, context: ExperienceContext) -> list[FormState]:
        states = []
        for form_definition in cls.forms:
            applicable = form_definition.is_applicable(context)
            visible = form_definition.is_visible(context)
            can_submit, reason = form_definition.availability(context)
            submission = context.submissions.get(form_definition.key)
            states.append(
                FormState(
                    definition=form_definition,
                    submission=submission,
                    applicable=applicable,
                    visible=visible,
                    can_submit=can_submit,
                    reason=reason,
                    action_label=_("Update") if submission else _("Complete"),
                    actions=tuple(form_definition.action_states(context)),
                ),
            )
        return states

    @classmethod
    def action_states(cls, context: ExperienceContext) -> list[ActionState]:
        return [
            ActionState(action, *action.availability(context)) for action in cls.actions
        ]

    @classmethod
    def required_forms_complete(cls, context: ExperienceContext) -> bool:
        return all(
            context.has_completed(form_definition.key)
            for form_definition in cls.forms
            if form_definition.required and form_definition.is_applicable(context)
        )

    @classmethod
    def calculate_progress(cls, context: ExperienceContext) -> ApplicationProgress:
        required = [
            form_definition
            for form_definition in cls.forms
            if form_definition.required and form_definition.is_applicable(context)
        ]
        completed = sum(
            context.has_completed(form_definition.key) for form_definition in required
        )
        total = len(required)
        percentage = round(completed / total * 100) if total else 100
        next_form_key = next(
            (
                form_definition.key
                for form_definition in required
                if not context.has_completed(form_definition.key)
            ),
            "",
        )
        return ApplicationProgress(
            completed=completed,
            total=total,
            percentage=percentage,
            next_form_key=next_form_key,
        )


COMMON_PERMISSIONS = (
    PermissionDefinition(
        permission_keys.VIEW_APPLICATION,
        _("View application"),
        _("Open the application and see its submitted information."),
        _("Application"),
    ),
    PermissionDefinition(
        permission_keys.EDIT_FORMS,
        _("Edit forms"),
        _("Complete and update application forms while they are editable."),
        _("Applicant"),
    ),
    PermissionDefinition(
        permission_keys.SUBMIT_APPLICATION,
        _("Submit application"),
        _("Send a completed application for review or resubmit changes."),
        _("Applicant"),
    ),
    PermissionDefinition(
        permission_keys.VIEW_QUERIES,
        _("View queries"),
        _("Read application questions and responses."),
        _("Queries"),
    ),
    PermissionDefinition(
        permission_keys.OPEN_QUERY,
        _("Ask review team"),
        _("Open an application question for the review team."),
        _("Queries"),
    ),
    PermissionDefinition(
        permission_keys.RESPOND_QUERIES,
        _("Respond to queries"),
        _("Post applicant responses in application conversations."),
        _("Queries"),
    ),
    PermissionDefinition(
        permission_keys.MANAGE_APPLICANT_ACCESS,
        _("Manage applicant access"),
        _("Assign applicant-side roles to organisation members."),
        _("Access"),
    ),
    PermissionDefinition(
        permission_keys.REVIEW_APPLICATION,
        _("Review application"),
        _("Open submitted forms and begin or continue review."),
        _("Review"),
    ),
    PermissionDefinition(
        permission_keys.RAISE_QUERY,
        _("Raise queries"),
        _("Ask the applicant for clarification or corrected evidence."),
        _("Review"),
    ),
    PermissionDefinition(
        permission_keys.RESOLVE_QUERY,
        _("Resolve queries"),
        _("Close application questions after they are handled."),
        _("Review"),
    ),
    PermissionDefinition(
        permission_keys.APPROVE_APPLICATION,
        _("Approve application"),
        _("Record a positive production-access decision."),
        _("Decision"),
    ),
    PermissionDefinition(
        permission_keys.REJECT_APPLICATION,
        _("Reject application"),
        _("Record a rejection and its reasons."),
        _("Decision"),
    ),
    PermissionDefinition(
        permission_keys.MANAGE_REVIEW_ACCESS,
        _("Manage review access"),
        _("Assign reviewer and decision-maker roles to OHC team members."),
        _("Access"),
    ),
)
