"""The Agent Skills published for the ABDM flows.

The skills themselves are read from `skills.json`, which
`manage.py fetch_agent_skills` writes from what the documentation site
publishes. Written here is only what that site cannot know: which of this
program's milestones each skill carries.
"""

from pathlib import Path

from ohc_experience.experiences.definitions import AgentSkillsDefinition
from ohc_experience.experiences.definitions import AgentTarget


class ABDMAgentSkills(AgentSkillsDefinition):
    licence = "MIT"
    repository = "nha-in/docs"
    branch = "main"
    source_path = "plugins/abdm-integrators-assistant/skills"
    base_url_setting = "ABDM_DOCS_URL"
    skills_path = "/skills"
    docs_path = "/docs/hiecm/v3/getting-started/build-with-ai"
    manifest_path = Path(__file__).with_name("skills.json")
    targets = {
        "claude": AgentTarget(
            "Claude Code",
            ".claude/skills/",
            "Claude Code reads it on the next session.",
            "claude://code/new?q={prompt}",
        ),
        "cursor": AgentTarget(
            "Cursor",
            ".cursor/skills/",
            "Cursor reads it on the next session.",
            "cursor://anysphere.cursor-deeplink/prompt?text={prompt}",
        ),
        "codex": AgentTarget(
            "Codex",
            ".codex/skills/",
            "Codex CLI reads it on the next session.",
        ),
        "copilot": AgentTarget(
            "Copilot",
            ".github/skills/",
            "Copilot reads it once it is committed.",
        ),
    }
    # FHIR belongs to M2, where a bundle is first pushed to a requester. PHR
    # application services certifies nothing itself, but only makes sense
    # inside a PHR app.
    milestones_by_skill = {
        "abdm-m1": ("m1",),
        "abdm-m2": ("m2",),
        "abdm-m3": ("m3",),
        "abdm-m4": ("m4",),
        "abdm-p1": ("p1",),
        "abdm-p2": ("p2",),
        "abdm-p3": ("p3", "p4"),
        "abdm-phr-services": ("p1",),
        "abdm-fhir": ("m2",),
    }
    limits = (
        "Every step cites the Catalogue entry behind it",
        "calls run against your sandbox credentials, never production",
        "a debug loop stops after five passes and asks",
    )
