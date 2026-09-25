"""The Agent Skills published for the ABDM flows.

The skills themselves are read from `skills.json`, which
`manage.py fetch_agent_skills` writes from what the documentation site
publishes. Written here is only what that site cannot know: which of this
program's milestones each skill carries, and which page its card links to.
"""

from pathlib import Path

from ohc_experience.experiences.definitions import AgentSkillsDefinition
from ohc_experience.experiences.definitions import AgentTarget
from ohc_experience.experiences.definitions import DeepLink

from .catalog import MILESTONES
from .docs import docs_page


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
        "copilot": AgentTarget(
            "Copilot",
            ".github/skills/",
            "Copilot reads it once it is committed.",
            (
                DeepLink(
                    "VS Code",
                    "vscode://GitHub.copilot-chat?mode=agent&prompt={prompt}",
                    encode_twice=True,
                ),
                # The Copilot app's session links must name the repository,
                # which the portal does not know. A chat needs none, and the
                # app asks before it sends the prompt.
                DeepLink("Copilot", "ghapp://chats/new?prompt={prompt}"),
            ),
        ),
        "codex": AgentTarget(
            "Codex",
            ".codex/skills/",
            "Codex reads it on the next session.",
            (DeepLink("Codex", "codex://new?prompt={prompt}"),),
        ),
        "cursor": AgentTarget(
            "Cursor",
            ".cursor/skills/",
            "Cursor reads it on the next session.",
            (
                DeepLink(
                    "Cursor",
                    "cursor://anysphere.cursor-deeplink/prompt?text={prompt}",
                ),
            ),
        ),
        "claude": AgentTarget(
            "Claude Code",
            ".claude/skills/",
            "Claude Code reads it on the next session.",
            (DeepLink("Claude Code", "claude://code/new?q={prompt}"),),
        ),
    }
    # Every ABDM and PHR call goes through the gateway. Subscriptions are the
    # HIU side. Scan and pay follows scan and share, which the HIP builds in M2
    # and the PHR app in P2. FHIR belongs to M2, where a bundle is first pushed
    # to a requester.
    milestones_by_skill = {
        "abdm-gateway": ("m1", "m2", "m3", "m4", "p1", "p2", "p3", "p4"),
        "abdm-m1": ("m1",),
        "abdm-m2": ("m2",),
        "abdm-m3": ("m3",),
        "abdm-m4": ("m4",),
        "abdm-p1": ("p1",),
        "abdm-p2": ("p2",),
        "abdm-p3": ("p3",),
        "abdm-p4": ("p4",),
        "abdm-subscription": ("m3",),
        "abdm-scan-and-pay": ("m2", "p2"),
        "abdm-fhir": ("m2",),
    }
    shared_skills = (
        "abdm-gateway",
        "abdm-subscription",
        "abdm-scan-and-pay",
        "abdm-fhir",
    )
    docs_by_skill = {
        "abdm-gateway": docs_page("/docs/hiecm/v3/concepts/gateway"),
        "abdm-m1": MILESTONES["m1"].docs_url,
        "abdm-m2": MILESTONES["m2"].docs_url,
        "abdm-m3": MILESTONES["m3"].docs_url,
        "abdm-m4": MILESTONES["m4"].docs_url,
        "abdm-p1": MILESTONES["p1"].docs_url,
        "abdm-p2": MILESTONES["p2"].docs_url,
        "abdm-p3": MILESTONES["p3"].docs_url,
        "abdm-p4": MILESTONES["p4"].docs_url,
        "abdm-subscription": docs_page("/reference/hiecm-subscription"),
        "abdm-scan-and-pay": docs_page("/reference/hiecm-scan-and-pay"),
        "abdm-fhir": docs_page("/docs/hiecm/v3/concepts/fhir"),
    }
    limits = (
        "Every step cites the Catalogue entry behind it",
        "calls run against your sandbox credentials, never production",
        "a debug loop stops after five passes and asks",
    )
