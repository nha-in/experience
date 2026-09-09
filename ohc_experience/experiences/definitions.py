from dataclasses import dataclass
from dataclasses import field
from datetime import date  # noqa: TC003
from graphlib import CycleError
from graphlib import TopologicalSorter
from typing import Any
from typing import ClassVar

from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError

from .models import FormReuseScope


class ApplicationFormDefinition:
    """Form identity, validation class and application-specific lifecycle hooks."""

    key: ClassVar[str]
    name: ClassVar[str]
    reuse_scope: ClassVar[str] = FormReuseScope.PRODUCT
    schema_version: ClassVar[int] = 1
    form_class: ClassVar[type]
    allow_approved_updates: ClassVar[bool] = False
    allow_reuse: ClassVar[bool] = False
    request_label = "application"
    submit_label = "Submit application"
    submitted_message = "Application submitted."
    approval_notice = ""

    @classmethod
    def initial_data(cls, item):
        return {}

    @classmethod
    def form_kwargs(cls, item):
        """Extra constructor arguments, e.g. product state a field gates on."""
        return {}

    @classmethod
    def submission_block_reason(cls, item):
        return ""

    @classmethod
    def approval_block_reason(cls, item):
        """Recheck time-sensitive evidence immediately before a decision."""
        return ""

    @classmethod
    def snapshot_valid_until(cls, form):
        """Optional validity date for this exact submission revision."""

    @classmethod
    def on_submit(cls, item, data, actor):
        """Project validated answers into implementation-specific state."""

    @classmethod
    def on_approve(cls, item, actor):
        """Return structured outcomes or perform implementation side effects."""
        return ()

    @classmethod
    def on_send_back(cls, item, actor):
        """Update implementation-specific state after a review is sent back."""


class ApplicationDefinition:
    """The application types supported by the current portal."""

    key: ClassVar[str]
    name: ClassVar[str]
    reference_prefix: ClassVar[str] = "APP"
    initial_status: ClassVar[str] = "draft"
    forms: ClassVar[tuple[type[ApplicationFormDefinition], ...]]
    success_statuses: ClassVar[frozenset[str]] = frozenset({"approved"})

    @classmethod
    def on_start(cls, application, actor):
        """Return any outcomes issued as soon as this application starts."""
        return ()

    @classmethod
    def validate(cls) -> None:
        keys = [form.key for form in cls.forms]
        if not cls.key or not keys or len(keys) != len(set(keys)):
            msg = "Applications require a key and uniquely named forms."
            raise ImproperlyConfigured(msg)
        for form in cls.forms:
            if not form.key or form.reuse_scope not in FormReuseScope.values:
                msg = "Each form requires a key and a supported reuse scope."
                raise ImproperlyConfigured(msg)


@dataclass(frozen=True)
class OutcomeDefinition:
    key: str
    name: str
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    field_schema: list[dict[str, Any]] = field(default_factory=list)
    valid_until: date | None = None
    status: str = "active"


@dataclass(frozen=True)
class MilestoneDefinition:
    key: str
    code: str
    name: str
    predecessor: str = ""


@dataclass(frozen=True)
class TrackDefinition:
    code: str
    name: str
    description: str
    keys: tuple[str, ...]


class CredentialDefinition:
    """A program supplies policy and copy; the chain provisions, the engine shows."""

    name = "Integration credentials"
    outcome_type = "integration_credentials"
    rotation_days = 90
    usage_notice = ""
    demo_notice = "Demo credentials are not valid on an external gateway."
    unavailable_notice = "Credentials are not available for this product yet."
    unavailable_heading = "Credentials pending"
    handoff_heading = ""
    handoff_notice = ""

    @classmethod
    def gateway_url(cls):
        return ""

    @classmethod
    def is_demo(cls):
        return False

    @classmethod
    def eligibility_error(cls, product):
        return ""


class ProgramDefinition:
    """Code-defined product workflow, catalog and portal presentation."""

    key: ClassVar[str]
    name = "Experience Manager"
    description = "Submit applications and manage your product workflows."
    short_name = "Experiences"
    brand_line_1 = "Experience"
    brand_line_2 = "Manager"
    header_name = "Experiences"
    review_heading = "Assessment"
    reviewer_name = "Reviewer"
    environment_name = "Application workspace"
    footer_note = ""
    docs_url = ""
    logo = ""
    authority_logo = ""
    authority_name = ""
    product_reference_prefix = "PRD"
    product_types: ClassVar[dict[str, str]] = {}
    solution_types: ClassVar[dict[str, str]] = {}
    organisation_form: ClassVar[type[ApplicationFormDefinition]]
    product_application: ClassVar[type[ApplicationDefinition]]
    milestone_application: ClassVar[type[ApplicationDefinition]]
    supplementary_applications: ClassVar[tuple[type[ApplicationDefinition], ...]] = ()
    certification_application: ClassVar[type[ApplicationDefinition] | None] = None
    milestones: ClassVar[dict[str, MilestoneDefinition]] = {}
    tracks: ClassVar[tuple[TrackDefinition, ...]] = ()
    credentials: ClassVar[type[CredentialDefinition] | None] = None
    signup_organisation_choices: ClassVar[tuple[tuple[str, str], ...]] = ()

    @classmethod
    def validate(cls):
        if not cls.key or len(cls.track_map()) != len(cls.tracks):
            msg = "Programs require a key and uniquely named tracks."
            raise ImproperlyConfigured(msg)
        if (
            cls.certification_application
            and cls.certification_application not in cls.supplementary_applications
        ):
            msg = "The certification application must be registered as supplementary."
            raise ImproperlyConfigured(msg)
        for key, milestone in cls.milestones.items():
            if key != milestone.key or (
                milestone.predecessor and milestone.predecessor not in cls.milestones
            ):
                msg = "Milestone keys and prerequisites must exist in the catalog."
                raise ImproperlyConfigured(msg)
        if any(key not in cls.milestones for track in cls.tracks for key in track.keys):
            msg = "Track milestones must exist in the catalog."
            raise ImproperlyConfigured(msg)
        try:
            cls.ordered_milestones()
        except CycleError as error:
            msg = "Milestone dependencies cannot contain a cycle."
            raise ImproperlyConfigured(msg) from error

    @classmethod
    def product_values(cls, data):
        return {key: data[key] for key in ("name", "description", "product_type")}

    @classmethod
    def certification_context(cls, product):
        """Optional product certification summary supplied by the program."""
        return {}

    @classmethod
    def milestone_keys(cls, selections):
        available = {
            f"{track.code}:{key}": key for track in cls.tracks for key in track.keys
        }
        if any(value not in available for value in selections):
            msg = "Choose milestones from the registered catalog."
            raise ValidationError(msg)
        keys = {available[value] for value in selections}
        for key in keys:
            prerequisite = cls.milestones[key].predecessor
            if prerequisite and prerequisite not in keys:
                msg = (
                    f"Select {cls.milestones[prerequisite].code} "
                    f"before {cls.milestones[key].name}."
                )
                raise ValidationError(msg)
        return keys

    @classmethod
    def ordered_milestones(cls):
        return tuple(
            TopologicalSorter(
                {
                    key: (item.predecessor,) if item.predecessor else ()
                    for key, item in cls.milestones.items()
                },
            ).static_order(),
        )

    @classmethod
    def track_map(cls):
        return {track.code: track for track in cls.tracks}

    @classmethod
    def on_product_created(cls, product, actor):
        """Run after the initial product registration has been submitted."""

    @classmethod
    def signup_metadata(cls, data):
        return {}

    @classmethod
    def seed_demo(cls, **options):
        raise NotImplementedError
