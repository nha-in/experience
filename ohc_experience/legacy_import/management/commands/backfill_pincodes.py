"""Put right the address answers the import filed wrong.

Two faults, both repaired from what the portal already stores:

The PIN. The import read `address.pin_code` and nothing else, so an
organisation whose address came from the NHCX enrolment row (`hcx_address`,
whose own `pin_code` column the import never selected) kept its address and
lost its PIN. An Indian address ends with its PIN.

The state and district. The import filed legacy's own spelling, which is upper
case and not what the form's list holds, so the form argues with its own data
on the next edit. Where they are missing altogether, a PIN that the import
filed with exactly one state and district says where that PIN is.
"""

import csv
import re
from collections import Counter
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from ohc_experience.experiences.models import FormSubmission
from ohc_experience.organisations import states

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


def repairs(data, pin_pairs):
    """The address answers to put right in one submission, as {field: value}."""
    fixed = {}
    pincode = data.get("pincode") or pincode_in(data.get("registered_address"))
    if pincode != (data.get("pincode") or ""):
        fixed["pincode"] = pincode
    state = data.get("state") or ""
    district = data.get("district") or ""
    pair = (
        states.canonical(state, district)
        if state and district
        else pin_pairs.get(pincode)
    )
    if pair and pair != (state, district):
        fixed["state"], fixed["district"] = pair
    return fixed


class Command(BaseCommand):
    help = "Put right the PIN code, state and district of imported verifications."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write the answers. Without it the run only says what it would.",
        )

    def imported(self):
        """The organisation verifications the import wrote, nobody else's."""
        return FormSubmission.objects.filter(
            form_key=ORGANISATION_FORM,
            metadata__imported=True,
        )

    def pin_pairs(self):
        """Each PIN the import filed with exactly one state and district.

        Read from the answers before anything is written, and with the PIN this
        run would fill counted in, so a dry run and a real run see one map.
        """
        found = defaultdict(set)
        for filed, state, district, address in self.imported().values_list(
            "data__pincode",
            "data__state",
            "data__district",
            "data__registered_address",
        ):
            pincode = filed or pincode_in(address)
            pair = states.canonical(state or "", district or "")
            if pincode and pair:
                found[pincode].add(pair)
        return {
            pincode: next(iter(pairs))
            for pincode, pairs in found.items()
            if len(pairs) == 1
        }

    def handle(self, *, commit, **options):
        pin_pairs = self.pin_pairs()
        report = csv.writer(self.stdout)
        report.writerow(
            ["submission", "organisation", "pincode", "state", "district", "address"],
        )
        counts = Counter()
        submissions = (
            self.imported()
            .defer("field_schema")
            .select_related("form__organisation")
            .order_by("pk")
        )
        with transaction.atomic():
            for submission in submissions.iterator():
                fixed = repairs(submission.data, pin_pairs)
                answers = {**submission.data, **fixed}
                counts["no PIN to find"] += not answers.get("pincode")
                counts["no state to find"] += not answers.get("state")
                counts["district not in the list"] += bool(
                    answers.get("district")
                    and not states.canonical(answers["state"], answers["district"]),
                )
                if not fixed:
                    continue
                counts["pincode"] += "pincode" in fixed
                if "state" in fixed:
                    key = "corrected" if submission.data.get("state") else "from a PIN"
                    counts[key] += 1
                organisation = submission.form.organisation
                report.writerow(
                    [
                        submission.pk,
                        organisation.name if organisation else "",
                        fixed.get("pincode", ""),
                        fixed.get("state", ""),
                        fixed.get("district", ""),
                        " ".join(
                            (submission.data.get("registered_address") or "").split(),
                        ),
                    ],
                )
                if commit:
                    submission.data.update(fixed)
                    # The import's own timestamps stay as they are.
                    submission.save(update_fields=["data"])
        self.stdout.write("")
        self.stdout.write("Written:" if commit else "To write:")
        self.stdout.write(f"  PIN codes filled: {counts['pincode']}")
        self.stdout.write(
            f"  State and district filled from a PIN: {counts['from a PIN']}",
        )
        self.stdout.write(f"  Legacy spellings corrected: {counts['corrected']}")
        self.stdout.write(f"Left without a PIN: {counts['no PIN to find']}")
        self.stdout.write(f"Left without a state: {counts['no state to find']}")
        self.stdout.write(
            f"Left with a district the list does not know: "
            f"{counts['district not in the list']}",
        )
        if not commit:
            self.stdout.write("Nothing was written. Pass --commit to write.")
