"""Server-side handoff to the legacy DHIS portal."""

from ohc_experience.integrations.dhis.handoff import DHIS_DESTINATION_URL
from ohc_experience.integrations.dhis.handoff import DHISConfig
from ohc_experience.integrations.dhis.handoff import DHISConfigurationError
from ohc_experience.integrations.dhis.handoff import build_dhis_url

__all__ = [
    "DHIS_DESTINATION_URL",
    "DHISConfig",
    "DHISConfigurationError",
    "build_dhis_url",
]
