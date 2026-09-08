from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import permission_keys
from .models import FormReuseScope
from .models import ProductOutcomeStatus
from .models import SubmissionStatus
from .registry import registry

if TYPE_CHECKING:
    from datetime import date

    from django.contrib.auth.base_user import AbstractBaseUser

    from .models import ApplicationFormUse
    from .models import ApplicationInstance
    from .models import FormAttachment
    from .models import FormRecord
    from .models import FormSubmission


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
class ProductOutcomeSpec:
    outcome_type: str
    name: str
    data: dict[str, Any]
    field_schema: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = ProductOutcomeStatus.ACTIVE
    valid_until: date | None = None


@dataclass(frozen=True)
class ActionResult:
    message: str
    new_status: str = ""
    metadata_updates: dict[str, Any] = field(default_factory=dict)
    outcome_updates: dict[str, Any] = field(default_factory=dict)
    product_outcomes: tuple[ProductOutcomeSpec, ...] = ()
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
    submissions: dict[str, FormSubmission]
    submission_history: dict[str, tuple[FormSubmission, ...]]
    form_records: dict[str, FormRecord]
    form_uses: dict[str, ApplicationFormUse]

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

    def form_history(self, form_key: str) -> tuple[FormSubmission, ...]:
        return self.submission_history.get(form_key, ())


@dataclass(frozen=True)
class FormActionState:
    definition: type[ApplicationFormAction]
    available: bool
    reason: str
    outcome_label: str = ""


@dataclass(frozen=True)
class FormState:
    definition: type[ApplicationFormDefinition]
    form_record: FormRecord
    form_use: ApplicationFormUse
    submission: FormSubmission | None
    applicable: bool
    visible: bool
    can_submit: bool
    reason: str
    action_label: str
    satisfied: bool
    renewal_due: bool
    submission_count: int
    version_count: int
    reused: bool
    used_by_count: int
    actions: tuple[FormActionState, ...] = ()

    @property
    def is_completed(self) -> bool:
        return self.satisfied

    @property
    def is_expired(self) -> bool:
        return bool(self.submission and self.submission.is_expired)


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
    reuse_scope: ClassVar[str] = FormReuseScope.PRODUCT
    required: ClassVar[bool] = True
    allow_updates: ClassVar[bool] = False
    repeatable: ClassVar[bool] = False
    valid_until_field: ClassVar[str] = ""
    renewal_window_days: ClassVar[int] = 0
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
            and not cls.repeatable
        ):
            return False, _("This form is locked after it is completed.")
        return True, ""

    @classmethod
    def is_complete(cls, context: ExperienceContext) -> bool:
        submission = context.submissions.get(cls.key)
        return bool(
            submission
            and submission.status == SubmissionStatus.COMPLETED
            and not submission.is_expired,
        )

    @classmethod
    def is_renewal_due(cls, context: ExperienceContext) -> bool:
        submission = context.submissions.get(cls.key)
        return bool(
            cls.repeatable
            and submission
            and submission.valid_until
            and submission.valid_until
            <= timezone.localdate() + timedelta(days=cls.renewal_window_days),
        )

    @classmethod
    def get_valid_until(cls, cleaned_data: dict[str, Any]):
        if not cls.valid_until_field:
            return None
        return cleaned_data.get(cls.valid_until_field)

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
        initial = dict(submission.data) if submission else {}
        for name, form_field in cls.form_class.base_fields.items():
            if isinstance(form_field, forms.FileField):
                initial.pop(name, None)
        return initial

    @classmethod
    def get_new_submission_initial(
        cls,
        context: ExperienceContext,
    ) -> dict[str, Any]:
        """Return defaults for a new occurrence, without copying prior answers."""
        return {}

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
        submission_mode: str | None = None,
    ) -> forms.Form:
        submission = context.submissions.get(cls.key)
        if submission_mode is None:
            submission_mode = (
                "renew" if cls.repeatable and submission is not None else "edit"
            )
        existing_files: dict[str, list[FormAttachment]] = {}
        if submission and submission_mode == "edit":
            for attachment in submission.attachments.filter(is_current=True):
                existing_files.setdefault(attachment.field_key, []).append(attachment)
        kwargs: dict[str, Any] = {
            "experience_context": context,
            "existing_files": existing_files,
        }
        if data is None:
            kwargs["initial"] = (
                cls.get_new_submission_initial(context)
                if submission_mode == "renew"
                else cls.get_initial(context)
            )
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
        submission: FormSubmission | None,
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
        submission: FormSubmission,
    ) -> tuple[bool, str]:
        return True, ""

    @classmethod
    def outcome_label(
        cls,
        context: ExperienceContext,
        submission: FormSubmission | None,
    ) -> str:
        return ""

    @classmethod
    def build_form(
        cls,
        *,
        context: ExperienceContext,
        submission: FormSubmission,
        data=None,
    ) -> forms.Form | None:
        if cls.form_class is None:
            return None
        return cls.form_class(data=data, experience_context=context)

    @classmethod
    def perform(
        cls,
        context: ExperienceContext,
        submission: FormSubmission,
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
    platform_observer_role_key: ClassVar[str] = "review_observer"
    dependency_satisfied_statuses: ClassVar[frozenset[str]] = frozenset(
        {"approved"},
    )
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
        cls._validate_platform_observer()
        if cls.initial_status not in collections["status"]:
            msg = f"{cls.key} has no initial status named {cls.initial_status}."
            raise ImproperlyConfigured(msg)

        unknown_dependency_statuses = cls.dependency_satisfied_statuses - set(
            collections["status"],
        )
        if unknown_dependency_statuses:
            msg = (
                f"{cls.key} has unknown dependency-satisfied statuses: "
                f"{unknown_dependency_statuses}"
            )
            raise ImproperlyConfigured(msg)
        cls._validate_forms(set(collections["form"]), permission_keys)

    @classmethod
    def _validate_platform_observer(cls) -> None:
        platform_observer = cls.get_role(cls.platform_observer_role_key)
        if platform_observer is None or platform_observer.audience != "platform":
            msg = (
                f"{cls.key} has no platform observer role named "
                f"{cls.platform_observer_role_key}."
            )
            raise ImproperlyConfigured(msg)

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
            if form_definition.reuse_scope not in FormReuseScope.values:
                msg = (
                    f"{cls.key}.{form_definition.key} has an unknown reuse scope "
                    f"named {form_definition.reuse_scope}."
                )
                raise ImproperlyConfigured(msg)
            if form_definition.valid_until_field and (
                form_definition.valid_until_field
                not in form_definition.form_class.base_fields
            ):
                msg = (
                    f"{cls.key}.{form_definition.key} has an unknown validity field "
                    f"named {form_definition.valid_until_field}."
                )
                raise ImproperlyConfigured(msg)
            if form_definition.renewal_window_days < 0:
                msg = f"{cls.key}.{form_definition.key} has a negative renewal window."
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
    def initial_product_outcomes(
        cls,
        application: ApplicationInstance,
        actor: AbstractBaseUser,
    ) -> tuple[ProductOutcomeSpec, ...]:
        return ()

    @classmethod
    def form_states(cls, context: ExperienceContext) -> list[FormState]:
        states = []
        for form_definition in cls.forms:
            applicable = form_definition.is_applicable(context)
            visible = form_definition.is_visible(context)
            can_submit, reason = form_definition.availability(context)
            form_record = context.form_records[form_definition.key]
            form_use = context.form_uses[form_definition.key]
            submission = context.submissions.get(form_definition.key)
            history = context.form_history(form_definition.key)
            renewal_due = form_definition.is_renewal_due(context)
            if submission is None:
                action_label = _("Complete")
            elif form_definition.repeatable:
                action_label = _("Renew") if renewal_due else _("Add submission")
            else:
                action_label = _("Update")
            states.append(
                FormState(
                    definition=form_definition,
                    form_record=form_record,
                    form_use=form_use,
                    submission=submission,
                    applicable=applicable,
                    visible=visible,
                    can_submit=can_submit,
                    reason=reason,
                    action_label=action_label,
                    satisfied=form_definition.is_complete(context),
                    renewal_due=renewal_due,
                    submission_count=len(
                        {item.submission_number for item in history},
                    ),
                    version_count=len(history),
                    reused=form_use.is_reused,
                    used_by_count=len(form_record.application_uses.all()),
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
    def unmet_dependencies(cls, context: ExperienceContext):
        return [
            application
            for application in context.application.dependencies.all()
            if application.status
            not in registry.get(
                application.application_type,
            ).dependency_satisfied_statuses
        ]

    @classmethod
    def required_forms_complete(cls, context: ExperienceContext) -> bool:
        return all(
            form_definition.is_complete(context)
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
            form_definition.is_complete(context) for form_definition in required
        )
        total = len(required)
        percentage = round(completed / total * 100) if total else 100
        next_form_key = next(
            (
                form_definition.key
                for form_definition in required
                if not form_definition.is_complete(context)
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
