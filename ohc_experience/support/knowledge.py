"""Related knowledge: the NHA documentation a ticket's category points at.

A static map, deliberately: the portal links to NHA's documentation rather
than restating it (design doc §2).
"""

from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from .models import Category

KNOWLEDGE = {
    Category.SANDBOX: [
        (_("Sandbox credentials and callback URLs"), "sandbox/credentials"),
        (_("Gateway session and token flow"), "hi-cm/authentication"),
    ],
    Category.API: [
        (_("Gateway API reference"), "hi-cm/apis"),
        (_("Callback request formats"), "hi-cm/callbacks"),
    ],
    Category.CERTIFICATION: [
        (_("Milestone requirements"), "hi-cm/m1"),
        (_("Functional testing and exit requests"), "sandbox/exit"),
    ],
    Category.DEPLOYMENT: [
        (_("Production onboarding after approval"), "production/onboarding"),
    ],
    Category.BILLING: [
        (_("Organisation verification"), "sandbox/organisation"),
    ],
}


def related_knowledge(category: str) -> list[dict]:
    base = settings.ABDM_DOCS_URL.rstrip("/")
    return [
        {"title": str(title), "url": f"{base}/{path}"}
        for title, path in KNOWLEDGE.get(category, [])
    ]
