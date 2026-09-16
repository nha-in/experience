from ohc_experience.experiences.definitions import ReferenceEnvironmentDefinition


class ABDMReferenceEnvironment(ReferenceEnvironmentDefinition):
    run_command = "docker run --rm -p 8000:8000 ghcr.io/nha-in/abdm-reference:latest"
    local_url = "http://localhost:8000"
    requirements = "Docker 24 or later, 4 GB memory available."
    built_on = (
        ("CARE", "images/marketing/care-logo-trim.svg"),
        ("Open Healthcare Network", "images/marketing/ohc-logo-trim.png"),
    )
    maintained_by = (("eGov Foundation", "images/marketing/egov-logo-trim.png"),)
    licence = "Digital Public Good · MIT licensed"
    flows = {
        "m1": (
            "Session and tokens",
            "ABHA creation by Aadhaar OTP",
            "ABHA login by mobile number",
            "Profile, ABHA card and QR code",
        ),
        "m2": (
            "HIP-initiated linking",
            "Notification to mobile",
            "Discovery and link",
            "Health record request and data transfer",
        ),
    }
