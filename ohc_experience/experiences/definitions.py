import re
from dataclasses import dataclass
from dataclasses import field
from datetime import date  # noqa: TC003
from graphlib import CycleError
from graphlib import TopologicalSorter
from pathlib import Path  # noqa: TC003
from typing import Any
from typing import ClassVar
from typing import NamedTuple
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError

from .models import FormReuseScope
from .models import ReviewItem
from .skills_manifest import read as read_skills_manifest


@dataclass(frozen=True)
class Prerequisite:
    """Something a review waits on before it can be decided."""

    name: str
    #: The review that settles it, when one exists.
    review: Any = None

    @property
    def withdrawn(self):
        """Submitted once, then taken back by the integrator to change."""
        return bool(
            self.review
            and self.review.status == ReviewItem.Status.DRAFT
            and self.review.submitted_at,
        )


class DocumentReadError(Exception):
    """A `read_document` hook could not read the upload; safe to show a user.

    `retryable` is False when the document itself is the problem, so that the
    browser can say so instead of offering an attempt that must fail again.
    """

    def __init__(self, message: str, *, retryable: bool = True):
        self.retryable = retryable
        super().__init__(message)


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
    #: What a reviewer rejecting this form chooses from. A form with no list
    #: takes the reviewer's note alone.
    reject_reasons: ClassVar[tuple[str, ...]] = ()

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
    def read_document(cls, field_key, upload, *, refresh=False):
        """Propose field values read out of a document the user just chose.

        Return `{form field name: value}`, empty where nothing could be read, or
        raise `DocumentReadError`. Nothing returned here is trusted: the fields
        stay editable and the form validates them again on save.

        `refresh` says the user chose the same document a second time, so a
        hook that remembers what it made of a document reads it again instead.
        """
        return {}

    @classmethod
    def on_submit(cls, item, data, actor):
        """Project validated answers into implementation-specific state."""

    @classmethod
    def on_approve(cls, item, actor):
        """Return structured outcomes or perform implementation side effects."""
        return ()

    @classmethod
    def on_reject(cls, item, actor):
        """Update implementation-specific state after a review is rejected."""

    @classmethod
    def on_withdraw(cls, item, actor):
        """Undo implementation-specific state after a request is withdrawn."""


class ApplicationDefinition:
    """The application types supported by the current portal."""

    key: ClassVar[str]
    name: ClassVar[str]
    filter_name: ClassVar[str] = ""
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
    #: Milestones shown alongside this one for context, without gating it. Unlike
    #: `predecessor`, none of these is required before this milestone can be
    #: submitted, decided, or, for an auto-approved one, recorded.
    related: tuple[str, ...] = ()
    #: Whether the gateway calls back for this milestone's flows. Only these
    #: need a callback URL, and therefore a bridge.
    needs_callback: bool = False


@dataclass(frozen=True)
class TrackDefinition:
    code: str
    name: str
    description: str
    keys: tuple[str, ...]
    docs_url: str = ""

    def prerequisites(self, milestones):
        """Other tracks' milestones this track cannot be submitted without.

        Walks each milestone's hard `predecessor` chain, stopping at this
        track's own milestones, since only what belongs to another track needs
        naming here. `related_milestones` is the same walk plus each
        milestone's non-blocking `related` milestones, for display.
        """

        def chain(key):
            predecessor = milestones[key].predecessor
            if not predecessor or predecessor in self.keys:
                return []
            return [*chain(predecessor), predecessor]

        return tuple(dict.fromkeys(key for own in self.keys for key in chain(own)))

    def related_milestones(self, milestones):
        """Other tracks' milestones this track builds on or is shown alongside.

        Superset of `prerequisites`: also walks each milestone's `related`
        milestones, which are named here for context but never enforced.
        """

        def chain(key):
            milestone = milestones[key]
            found = []
            for other in (milestone.predecessor, *milestone.related):
                if other and other not in self.keys:
                    found.extend(chain(other))
                    found.append(other)
            return found

        return tuple(dict.fromkeys(key for own in self.keys for key in chain(own)))


@dataclass(frozen=True)
class SupportCategoryDefinition:
    """One entry in a program's support menu, and the unit a support grant names.

    Support is filed and permissioned more finely than it is reviewed: a track
    such as HIE-CM answers for four milestones at once, while the people who
    answer M1 identity questions are rarely the ones who answer M4 registry
    ones. So the support area gets its own vocabulary, and each category names
    the ``track`` it belongs to, which is what still ties a ticket back to the
    milestones a product applied for and to that track's documentation.

    ``issue_types`` is the sub-menu shown once a category is chosen. It labels
    the ticket for triage and carries no permission of its own: granting at that
    depth would multiply the permission grid without answering a question
    anyone asks of it.
    """

    code: str
    name: str
    track: str = ""
    issue_types: tuple[str, ...] = ()
    description: str = ""

    @property
    def summary(self) -> str:
        """What this category covers, for whoever hands out the permission."""
        return self.description or readable_list(self.issue_types)


class CredentialDefinition:
    """Copy for one kind of credential on a product's Credentials page."""

    name = "Integration credentials"
    usage_notice = ""
    unavailable_notice = "Credentials are not available for this product yet."
    unavailable_heading = "Credentials pending"


class SandboxCredentialDefinition(CredentialDefinition):
    """A program supplies policy and copy; the chain provisions, the engine shows."""

    outcome_type = "integration_credentials"
    demo_notice = "Demo credentials are not valid on an external gateway."

    @classmethod
    def gateway_url(cls):
        return ""

    @classmethod
    def is_demo(cls):
        return False


class ProductionCredentialDefinition(CredentialDefinition):
    """Staff add each product's production client ID once an exit is approved.

    The portal holds the ID and its issue date; the secret is issued outside it.
    """

    name = "Production credentials"
    unavailable_notice = (
        "Production credentials become available once a milestone exit is approved."
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


class ReferenceShell(NamedTuple):
    """How to run the reference environment from one kind of terminal."""

    label: str
    prompt: str
    #: `{client_id}` and `{client_secret}` mark where the credentials go, inside
    #: single quotes, and `{options}` where the chosen milestones' options go.
    command: str
    #: How a single quote is written inside a single-quoted value.
    single_quote: str


class ReferenceEnvironmentDefinition:
    """A runnable implementation of the program's flows on synthetic data."""

    #: Run commands by shell key. The first is shown by default.
    shells: ClassVar[dict[str, ReferenceShell]] = {}
    stop_command = ""
    local_url = ""
    requirements = ""
    includes = ""
    #: Demo sign-in as (username, password).
    sign_in: ClassVar[tuple[str, str] | None] = None
    #: Logos as (name, static path) pairs.
    built_on: ClassVar[tuple[tuple[str, str], ...]] = ()
    maintained_by: ClassVar[tuple[tuple[str, str], ...]] = ()
    licence = ""
    #: Flow names by milestone key.
    flows: ClassVar[dict[str, tuple[str, ...]]] = {}
    #: Command options that add a milestone, by milestone key. Others always run.
    milestone_options: ClassVar[dict[str, str]] = {}
    #: Milestones whose flows are still being built.
    in_progress: ClassVar[tuple[str, ...]] = ()
    #: Stand-ins for credentials that are not entered yet. Plain words, so a shell
    #: reads them as text if they are run unchanged.
    client_id_placeholder = "YOUR_CLIENT_ID"
    client_secret_placeholder = "YOUR_CLIENT_SECRET"  # noqa: S105

    @classmethod
    def command_segments(cls, shell, client_id=""):
        """A shell's command as (slot, text) pairs, in order.

        The slot is empty for plain text. A `client_id` or `client_secret` slot
        holds the credential, or its placeholder, for the page to fill in. An
        `options` slot marks where milestone options go and has no text.
        """
        values = {
            "client_id": client_id.replace("'", shell.single_quote)
            or cls.client_id_placeholder,
            "client_secret": cls.client_secret_placeholder,
            "options": "",
        }
        parts = re.split(r"\{(client_id|client_secret|options)\}", shell.command)
        return [
            (part, values[part]) if index % 2 else ("", part)
            for index, part in enumerate(parts)
            if index % 2 or part
        ]


class DeepLink(NamedTuple):
    """An app an agent runs in, and the URL that opens it with a prompt."""

    #: Named on the link's button.
    app: str
    #: A format string that takes one `{prompt}`.
    url: str
    #: VS Code decodes a link's query once before it reads the parameters, so a
    #: prompt encoded only once would be cut at the command's first `&`.
    encode_twice: bool = False


class AgentTarget(NamedTuple):
    """A coding agent, and the folder it reads installed Agent Skills from."""

    label: str
    #: Ends in a slash: the skill's folder name is appended to it.
    directory: str
    #: How this agent picks the skill up, said in one line.
    note: str = ""
    #: The apps that open with the install prompt, one button each. Empty for an
    #: agent with no URL scheme, which is then offered the command to copy but no
    #: one-click link.
    deeplinks: tuple[DeepLink, ...] = ()


class AgentSkillsDefinition:
    """The Agent Skills a program offers, as the documentation site lists them.

    The skills are published elsewhere; only the milestone mapping is ours.
    """

    licence = ""
    #: Where the skills are written, as `owner/repo` and the path inside it.
    repository = ""
    branch = "main"
    source_path = ""
    #: Setting naming the documentation site this deployment installs from.
    base_url_setting = ""
    skills_path = ""
    docs_path = ""
    #: The file `manage.py fetch_agent_skills` writes the published list into.
    manifest_path: ClassVar[Path | None] = None
    #: The page fills `{skill}` and `{sections}` from whichever skill is chosen.
    install_command = (
        "mkdir -p {directory}{skill}/references"
        " && curl -fsSL {skills_url}/{skill}/SKILL.md"
        " -o {directory}{skill}/SKILL.md"
        " && for f in {sections}; do"
        " curl -fsSL {skills_url}/{skill}/references/$f.md"
        " -o {directory}{skill}/references/$f.md; done"
    )
    #: What a deeplink hands the agent, above the line it runs. `{skill}` is the
    #: chosen skill's title, `{command}` its install command. The reader sees it
    #: before it runs: most apps only fill the composer, and the Copilot app asks
    #: before it sends. The guard is for a link opened outside the repository, as
    #: a chat in the Copilot app always is.
    install_prompt = (
        "Set this repository up with the {skill} Agent Skill, then build from"
        " it. Run:\n\n{command}\n\n"
        "If this is not the repository you mean to set up, ask me for the path"
        " before you write anything."
    )
    #: Install targets by key. The first is shown by default.
    targets: ClassVar[dict[str, AgentTarget]] = {}
    #: Milestones each published skill carries, by folder name. A skill this
    #: does not name is offered to everybody.
    milestones_by_skill: ClassVar[dict[str, tuple[str, ...]]] = {}
    #: The documentation page each skill's card links to, by folder name.
    docs_by_skill: ClassVar[dict[str, str]] = {}
    #: Skills that are no milestone of their own, listed apart from the tracks.
    shared_skills: ClassVar[tuple[str, ...]] = ()
    #: What a skill holds itself to, shown beside the command.
    limits: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def base_url(cls):
        """The documentation site, as this deployment is pointed at it."""
        if not cls.base_url_setting:
            return ""
        return getattr(settings, cls.base_url_setting, "").rstrip("/")

    @classmethod
    def skills_url(cls):
        """Where the skills are fetched from, without a trailing slash."""
        base = cls.base_url()
        return f"{base}{cls.skills_path}" if base else ""

    @classmethod
    def docs_url(cls):
        """The page that explains what Agent Skills are."""
        base = cls.base_url()
        return f"{base}{cls.docs_path}" if base else ""

    @classmethod
    def skills(cls):
        """Every published skill, with the milestones and docs this program gives it."""
        if cls.manifest_path is None:
            return ()
        return tuple(
            skill
            | {
                "milestones": cls.milestones_by_skill.get(skill["slug"], ()),
                "docs_url": cls.docs_by_skill.get(skill["slug"], ""),
                "shared": skill["slug"] in cls.shared_skills,
            }
            for skill in read_skills_manifest(cls.manifest_path)
        )

    @classmethod
    def source_url(cls, skill=None):
        """Where the skills can be read without installing them."""
        if not cls.repository:
            return ""
        path = f"{cls.source_path}/{skill['slug']}" if skill else cls.source_path
        return f"https://github.com/{cls.repository}/tree/{cls.branch}/{path}"

    @classmethod
    def command_segments(cls, target):
        """A target's install command as (slot, text) pairs, in order.

        An empty slot is plain text; `skill` and `sections` are filled by the page.
        """
        command = cls.install_command.format(
            repository=cls.repository,
            source_path=cls.source_path,
            skills_url=cls.skills_url(),
            directory=target.directory,
            skill="{skill}",
            sections="{sections}",
        )
        parts = re.split(r"\{(skill|sections)\}", command)
        return [
            (part, "") if index % 2 else ("", part)
            for index, part in enumerate(parts)
            if index % 2 or part
        ]

    @classmethod
    def install_line(cls, target, skill):
        """The install command for one agent and one skill, filled in full."""
        return cls.install_command.format(
            repository=cls.repository,
            source_path=cls.source_path,
            skills_url=cls.skills_url(),
            directory=target.directory,
            skill=skill["slug"],
            sections=" ".join(skill["sections"]),
        )

    @classmethod
    def install_deeplink(cls, link, target, skill):
        """A one-click link that opens `link`'s app with the prompt that installs
        the skill into `target`'s folder."""
        prompt = cls.install_prompt.format(
            skill=skill["title"],
            command=cls.install_line(target, skill),
        )
        encoded = quote(prompt, safe="")
        if link.encode_twice:
            encoded = quote(encoded, safe="")
        return link.url.format(prompt=encoded)

    @classmethod
    def validate(cls, milestones):
        if not cls.targets:
            msg = "Agent Skills need an agent to install them into."
            raise ImproperlyConfigured(msg)
        if not cls.base_url():
            msg = (
                "Agent Skills need a documentation site to be installed from. "
                f"Set {cls.base_url_setting or 'base_url_setting'} for this "
                "deployment."
            )
            raise ImproperlyConfigured(msg)
        # The file is not read here: it is data, and a refresh that disagreed
        # with the mapping would stop the portal booting. A test checks that.
        for slug, keys in cls.milestones_by_skill.items():
            unknown = set(keys) - set(milestones)
            if unknown:
                msg = (
                    f"Agent Skill {slug!r} names milestones that are not "
                    f"in the catalog: {readable_list(sorted(unknown))}."
                )
                raise ImproperlyConfigured(msg)


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
    environment_name = "Application environment"
    footer_note = ""
    #: The documentation site itself, for links outside the section docs_url
    #: names. Templates build their own paths from it.
    docs_site = ""
    docs_url = ""
    #: Where "Milestone documentation" points. Falls back to docs_url.
    milestones_docs_url = ""
    logo = ""
    authority_logo = ""
    authority_name = ""
    product_reference_prefix = "PRD"
    solution_types: ClassVar[dict[str, str]] = {}
    organisation_form: ClassVar[type[ApplicationFormDefinition]]
    applications: ClassVar[ApplicationSet]
    milestones: ClassVar[dict[str, MilestoneDefinition]] = {}
    tracks: ClassVar[tuple[TrackDefinition, ...]] = ()
    support_categories: ClassVar[tuple[SupportCategoryDefinition, ...]] = ()
    sandbox_credentials: ClassVar[type[SandboxCredentialDefinition] | None] = None
    production_credentials: ClassVar[type[ProductionCredentialDefinition] | None] = None
    handoffs: ClassVar[dict[str, type[ProductHandoffDefinition]]] = {}
    reference_environment: ClassVar[type[ReferenceEnvironmentDefinition] | None] = None
    agent_skills: ClassVar[type[AgentSkillsDefinition] | None] = None
    signup_organisation_choices: ClassVar[tuple[tuple[str, str], ...]] = ()

    @classmethod
    def track_milestones(cls, track):
        """What a track shows: related milestones from other tracks, then its own."""
        return (*track.related_milestones(cls.milestones), *track.keys)

    @classmethod
    def applied_keys(cls, track, selections):
        """A product's chosen milestones on this track, with their related ones."""
        chosen = [key for key in track.keys if f"{track.code}:{key}" in selections]
        if not chosen:
            return []
        return [*track.related_milestones(cls.milestones), *chosen]

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
    def _validate_tracks(cls):
        """Every track milestone exists, and its prerequisite and any related
        milestone are each offered somewhere."""
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
                for related in cls.milestones[key].related:
                    if related not in offered:
                        msg = (
                            f"Track {track.code!r} lists {key!r}, but no track "
                            f"offers its related milestone {related!r}."
                        )
                        raise ImproperlyConfigured(msg)

    @classmethod
    def validate(cls):
        if not cls.key or len(cls.track_map()) != len(cls.tracks):
            msg = "Programs require a key and uniquely named tracks."
            raise ImproperlyConfigured(msg)
        if not cls.applications.overrides.keys() <= cls.milestones.keys():
            msg = "Application overrides must name a milestone in the catalog."
            raise ImproperlyConfigured(msg)
        for key, milestone in cls.milestones.items():
            predecessor = milestone.predecessor
            if (
                key != milestone.key
                or (predecessor and predecessor not in cls.milestones)
                or any(other not in cls.milestones for other in milestone.related)
            ):
                msg = (
                    "Milestone keys, prerequisites and related milestones must "
                    "exist in the catalog."
                )
                raise ImproperlyConfigured(msg)
        cls._validate_tracks()
        if cls.agent_skills is not None:
            cls.agent_skills.validate(cls.milestones)
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
    def support_category_map(cls):
        """Support's own menu, or one category per track when none is declared."""
        categories = cls.support_categories or tuple(
            SupportCategoryDefinition(track.code, track.name, track=track.code)
            for track in cls.tracks
        )
        return {category.code: category for category in categories}

    @classmethod
    def grant_categories(cls, area):
        """(code, label, description) rows a grant in this area may name.

        Support answers for its own categories; review and events still answer
        for tracks, with the blank row for work that belongs to no track.
        """
        if area == "support" and cls.support_categories:
            return tuple(
                (category.code, category.name, category.summary)
                for category in cls.support_categories
            )
        return (
            ("", "General / onboarding", "Organisation and product registration"),
            *((track.code, track.code, track.name) for track in cls.tracks),
        )

    @classmethod
    def on_product_created(cls, product, actor):
        """Run after the initial product registration has been submitted."""

    @classmethod
    def seed_demo(cls, **options):
        raise NotImplementedError
