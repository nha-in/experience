"""Put back the PIN codes the import left out.

The import read `address.pin_code` and nothing else, so an organisation whose
address came from the NHCX enrolment row (`hcx_address`, a table whose own
`pin_code` column the import never selected) kept its address and lost its PIN.
The address is in the portal already, and an Indian one ends with its PIN, so
no second look at legacy is needed to put the answer back.
"""

import csv
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from ohc_experience.experiences.models import FormSubmission

ORGANISATION_FORM = "sandbox_organisation"
#: A PIN code as the verification form takes it, found inside a line. The word
#: boundaries refuse a longer number and digits glued to letters, so a phone
#: number and a GSTIN are left alone.
PINCODE = re.compile(r"\b[1-9][0-9]{5}\b")


def pincode_in(address):
    """The PIN an address ends with, or "" when it holds none.

    The last match, because an address ends with its PIN and what comes before
    it may be a plot or building number of the same length.
    """
    found = PINCODE.findall(address or "")
    return found[-1] if found else ""


class Command(BaseCommand):
    help = "Fill the blank PIN code of imported organisation verifications."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write the PIN codes. Without it the run only says what it would.",
        )

    def handle(self, *, commit, **options):
        imported = (
            FormSubmission.objects.filter(
                form_key=ORGANISATION_FORM,
                metadata__imported=True,
            )
            .defer("field_schema")
            .select_related("form__organisation")
            .order_by("pk")
        )
        report = csv.writer(self.stdout)
        report.writerow(["submission", "organisation", "pincode", "address"])
        filled = 0
        no_pin = 0
        with transaction.atomic():
            for submission in imported.iterator():
                if submission.data.get("pincode"):
                    continue
                address = submission.data.get("registered_address") or ""
                pincode = pincode_in(address)
                if not pincode:
                    no_pin += 1
                    continue
                organisation = submission.form.organisation
                report.writerow(
                    [
                        submission.pk,
                        organisation.name if organisation else "",
                        pincode,
                        " ".join(address.split()),
                    ],
                )
                filled += 1
                if commit:
                    submission.data["pincode"] = pincode
                    # The import's own timestamps stay as they are.
                    submission.save(update_fields=["data"])
        self.stdout.write("")
        self.stdout.write(f"{'Filled' if commit else 'Would fill'}: {filled}")
        self.stdout.write(f"Left blank, no PIN in the address: {no_pin}")
        if not commit:
            self.stdout.write("Nothing was written. Pass --commit to write.")
