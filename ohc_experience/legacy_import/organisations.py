"""Which legacy registrations belong to one organisation.

Legacy signed an email in to its newest registration, and only let it register
again once the last was rejected, so the email is the account: every
registration under one email becomes one organisation.
"""

from dataclasses import dataclass
from dataclasses import field

from . import clean

#: `ApplicationType.ORGANIZATION` in legacy's enums, beside admin and individual.
ORGANISATION_APPLICATION = "3"


@dataclass
class Integrator:
    """One email's registrations, which become one organisation."""

    email: str
    rows: list = field(default_factory=list)

    @property
    def is_person(self):
        """Whether every registration under this email was an individual's."""
        return all(
            row["application_type"] != ORGANISATION_APPLICATION for row in self.rows
        )


def read_integrators(rows):
    """One integrator per email address, its registrations oldest first."""
    integrators = {}
    for row in rows:
        email = clean.email(row["email"])
        integrators.setdefault(email, Integrator(email=email)).rows.append(row)
    for integrator in integrators.values():
        integrator.rows.sort(
            key=lambda row: (clean.when(row["created_at"]), row["sd_id"]),
        )
    return list(integrators.values())
