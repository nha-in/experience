from ohc_experience.experiences.definitions import ReferenceEnvironmentDefinition
from ohc_experience.experiences.definitions import ReferenceShell

# CARE and care_fe are built from their latest develop on every run, and care-abdm
# from a pinned commit. The compose file lives on a fork of care_create until its
# origin carries it.
COMPOSE_FILE = (
    "https://github.com/amjithtitus09/care_create.git#reference:reference/compose.yaml"
)
RUN = f"docker compose -f {COMPOSE_FILE} {{options}}up --build --wait --yes"


class ABDMReferenceEnvironment(ReferenceEnvironmentDefinition):
    shells = {
        "posix": ReferenceShell(
            "macOS and Linux",
            "$",
            "ABDM_CLIENT_ID={client_id} ABDM_CLIENT_SECRET={client_secret} " + RUN,
        ),
        "powershell": ReferenceShell(
            "Windows PowerShell",
            ">",
            '$env:ABDM_CLIENT_ID="{client_id}"; '
            '$env:ABDM_CLIENT_SECRET="{client_secret}"; ' + RUN,
        ),
    }
    stop_command = "docker compose -p care-reference down"
    local_url = "http://localhost:4400"
    requirements = (
        "Docker with Compose 2.37 or later. The first run takes about 10 minutes."
    )
    includes = (
        "CARE with the care-abdm plug and demo data. CARE and care_fe are built from "
        "their latest code on every run; care-abdm is pinned to its M1 version for now."
    )
    sign_in = ("care-admin", "Ohcn@123")
    built_on = (
        ("CARE", "images/marketing/care-logo-trim.svg"),
        ("Open Healthcare Network", "images/marketing/ohc-logo-trim.png"),
    )
    maintained_by = (("eGov Foundation", "images/marketing/egov-logo-trim.png"),)
    licence = "Digital Public Good · MIT licensed"
    # The flows care-abdm implements, milestone by milestone.
    flows = {
        "m1": (
            "Session and tokens",
            "ABHA creation by Aadhaar OTP",
            "ABHA login by mobile, Aadhaar, ABHA number or address",
            "Profile and ABHA card",
        ),
        "m2": (
            "HIP-initiated linking",
            "Notification to mobile",
            "Discovery and link",
            "Health record request and data transfer",
        ),
    }
    # M2 opens a public tunnel for ABDM callbacks.
    milestone_options = {"m2": "--profile m2"}
    in_progress = ("m2",)
