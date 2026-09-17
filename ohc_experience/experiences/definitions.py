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


@dataclass(frozen=True)
class Prerequisite:
    """Something a review waits on before it can be decided."""

    name: str
    #: The review that settles it, when one exists.
    review: Any = None


class ApplicationFormDefinition:
    """Form identity, validation class and application-specific lifecycle hooks."""

    key: ClassVar[str]
    name: ClassVar[str]
    reuse_scope: ClassVar[str] = FormReuseScope.PRODUCT
    schema_version: ClassVar[int] = 1
    form_class: ClassVar[type]
    allow_approved_updates: ClassVar[bool] = False
    allow_reuse: ClassVar[bool] = False
    #: Submitting is the whole process — no reviewer decides it. It is recorded
    #: as soon as its prerequisites are approved, and until then it waits in
    #: the queue, so the record is visible either way.
    auto_approve: ClassVar[bool] = False
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
    def pending_prerequisites(cls, item):
        """Approvals outside the application's dependencies that are still due.

        Return `Prerequisite` rows. The engine adds unapproved dependencies itself.
        """
        return ()

    @classmethod
    def prerequisites_due(cls):
        """The reviews `pending_prerequisites` names something for, as a filter.

        Return a `ReviewItem` Q, or None when there is nothing beyond the
        dependencies. The review queue sorts waiting reviews from ready ones with
        it, so it must agree with `pending_prerequisites`.
        """

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

    @classmethod
    def on_withdraw(cls, item, actor):
        """Undo implementation-specific state after a request is withdrawn."""


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
class ApplicationSet:
    """The application types a program registers, by the role each one plays."""

    product: type[ApplicationDefinition]
    milestone: type[ApplicationDefinition]
    certification: type[ApplicationDefinition] | None = None
    #: Milestones whose request is not the usual exit evidence.
    overrides: dict[str, type[ApplicationDefinition]] = field(default_factory=dict)

    def all(self) -> tuple[type[ApplicationDefinition], ...]:
        return tuple(
            dict.fromkeys(
                item
                for item in (
                    self.product,
                    self.milestone,
                    self.certification,
                    *self.overrides.values(),
                )
                if item is not None
            ),
        )


@dataclass(frozen=True)
class OutcomeDefinition:
    key: str
    name: str
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    field_schema: list[dict[str, Any]] = field(default_factory=list)
    valid_until: date | None = None
    status: str = "active"


def readable_list(names):
    """["a", "b", "c"] -> "a, b and c"."""
    names = list(names)
    if not names:
        return ""
    *rest, last = names
    return f"{', '.join(rest)} and {last}" if rest else last


@dataclass(frozen=True)
class MilestoneDefinition:
    key: str
    code: str
    name: str
    predecessor: str = ""
    description: str = ""
    docs_url: str = ""


@dataclass(frozen=True)
class TrackDefinition:
    code: str
    name: str
    description: str
    keys: tuple[str, ...]
    docs_url: str = ""

    def prerequisites(self, milestones):
        """Other tracks' milestones this track builds on, directly or not."""

        def chain(key):
            predecessor = milestones[key].predecessor
            if not predecessor or predecessor in self.keys:
                return []
            return [*chain(predecessor), predecessor]

        return tuple(dict.fromkeys(key for own in self.keys for key in chain(own)))


class CredentialDefinition:
    """Copy for one kind of credential on a product's Credentials page."""

    name = "Integration credentials"
    usage_notice = ""
    unavailable_notice = "Credentials are not available for this product yet."
    unavailable_heading = "Credentials pending"


class SandboxCredentialDefinition(CredentialDefinition):
    """A program supplies policy and copy; the chain provisions, the engine shows."""

    outcome_type = "integration_credentials"
    rotation_days = 90
    demo_notice = "Demo credentials are not valid on an external gateway."

    @classmethod
    def gateway_url(cls):
        return ""

    @classmethod
    def is_demo(cls):
        return False


class ProductionCredentialDefinition(CredentialDefinition):
    """Staff record each product's production client ID once an exit is approved.

    The portal holds the ID alone; the secret is issued outside it.
    """

    name = "Production access"
    unavailable_notice = (
        "Production access becomes available once a milestone exit is approved."
    )
    pending_notice = (
        "Your exit is approved. Your production client ID will appear here once "
        "it is issued."
    )


class ProductHandoffDefinition:
    """A program-owned external handoff, initiated through the product portal."""

    name = "Continue to external service"
    description = ""
    action_label = "Continue"

    @classmethod
    def options(cls, product, *, actor):
        """Return key, label, enabled and reason rows without creating tokens."""
        return ()

    @classmethod
    def create_url(cls, product, *, option, actor):
        """Recheck permission and eligibility, then return the destination URL."""
        raise NotImplementedError


class ReferenceEnvironmentDefinition:
    """A runnable implementation of the program's flows on synthetic data."""

    run_command = ""
    local_url = ""
    requirements = ""
    #: Logos as (name, static path) pairs.
    built_on: ClassVar[tuple[tuple[str, str], ...]] = ()
    maintained_by: ClassVar[tuple[tuple[str, str], ...]] = ()
    licence = ""
    #: Flow names by milestone key.
    flows: ClassVar[dict[str, tuple[str, ...]]] = {}


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
    solution_types: ClassVar[dict[str, str]] = {}
    organisation_form: ClassVar[type[ApplicationFormDefinition]]
    applications: ClassVar[ApplicationSet]
    milestones: ClassVar[dict[str, MilestoneDefinition]] = {}
    tracks: ClassVar[tuple[TrackDefinition, ...]] = ()
    sandbox_credentials: ClassVar[type[SandboxCredentialDefinition] | None] = None
    production_credentials: ClassVar[type[ProductionCredentialDefinition] | None] = None
    handoffs: ClassVar[dict[str, type[ProductHandoffDefinition]]] = {}
    reference_environment: ClassVar[type[ReferenceEnvironmentDefinition] | None] = None
    signup_organisation_choices: ClassVar[tuple[tuple[str, str], ...]] = ()

    @classmethod
    def track_milestones(cls, track):
        """What a track shows: prerequisites from other tracks, then its own."""
        return (*track.prerequisites(cls.milestones), *track.keys)

    @classmethod
    def applied_keys(cls, track, selections):
        """A product's chosen milestones on this track, with their prerequisites."""
        chosen = [key for key in track.keys if f"{track.code}:{key}" in selections]
        if not chosen:
            return []
        return [*track.prerequisites(cls.milestones), *chosen]

    @classmethod
    def tracks_with(cls, milestone_key):
        return tuple(
            track
            for track in cls.tracks
            if milestone_key in cls.track_milestones(track)
        )

    @classmethod
    def shared_with(cls, milestone_key, track_code=""):
        return tuple(
            track.code
            for track in cls.tracks_with(milestone_key)
            if track.code != track_code
        )

    @classmethod
    def shared_note(cls, track_code):
        """ "M1 is shared with UHI and PHR." Empty when this track shares nothing."""
        track = cls.track_map().get(track_code)
        if track is None:
            return ""
        sentences = []
        for key in cls.track_milestones(track):
            others = cls.shared_with(key, track_code)
            if others:
                code = cls.milestones[key].code
                sentences.append(f"{code} is shared with {readable_list(others)}.")
        return " ".join(sentences)

    @classmethod
    def application_for(cls, milestone_key):
        return cls.applications.overrides.get(
            milestone_key,
            cls.applications.milestone,
        )

    @classmethod
    def validate(cls):
        if not cls.key or len(cls.track_map()) != len(cls.tracks):
            msg = "Programs require a key and uniquely named tracks."
            raise ImproperlyConfigured(msg)
        if not cls.applications.overrides.keys() <= cls.milestones.keys():
            msg = "Application overrides must name a milestone in the catalog."
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
        offered = {key for track in cls.tracks for key in track.keys}
        for track in cls.tracks:
            for key in track.keys:
                predecessor = cls.milestones[key].predecessor
                if predecessor and predecessor not in offered:
                    msg = (
                        f"Track {track.code!r} lists {key!r}, but no track offers "
                        f"its predecessor {predecessor!r}, so it can never unlock."
                    )
                    raise ImproperlyConfigured(msg)
        try:
            cls.ordered_milestones()
        except CycleError as error:
            msg = "Milestone dependencies cannot contain a cycle."
            raise ImproperlyConfigured(msg) from error

    @classmethod
    def product_values(cls, data):
        return {key: data[key] for key in ("name", "description")}

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
    def longest_chain(cls):
        """The most milestones any one builds on, directly or not."""
        depth = {}
        for key in cls.ordered_milestones():
            predecessor = cls.milestones[key].predecessor
            depth[key] = depth[predecessor] + 1 if predecessor else 0
        return max(depth.values(), default=0)

    @classmethod
    def track_map(cls):
        return {track.code: track for track in cls.tracks}

    @classmethod
    def on_product_created(cls, product, actor):
        """Run after the initial product registration has been submitted."""

    @classmethod
    def seed_demo(cls, **options):
        raise NotImplementedError
