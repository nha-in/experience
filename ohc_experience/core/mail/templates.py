"""Every gateway email this portal sends, and the template ABDM approved for it.

An ID and the body that fills it are one unit: NHA registers the wording
against the ID, so the two have to move together, in one commit, through one
review. Keeping the IDs here rather than in the environment is what makes that
possible, and `ohc_experience/integrations/notification/templates.py` does the
same for the one-time codes.

Add an email by adding its purpose here and the body template it renders.
The IDs below are the ones NHA issued against "ABDM Template Request v1.0";
read the registered wording from `/internal/v3/notification/template/id/{id}`
on notification-db, which only an in-VPC caller can reach.

`GLOBAL_EMAIL_TEMPLATE_IDS` still overrides individual purposes, for an
environment whose gateway registered different IDs.
"""

APPROVED_TEMPLATE_IDS = {
    # Review thread updates and the decision that closes them.
    "review": "1077013850031295815",
    # Support ticket thread entries sent to the support inbox.
    "support_ticket": "1077013850031295816",
    # Registered as "Event notice": event registration and its 24-hour
    # reminder. Two notices have no purpose of their own and fall back to this
    # one, so they still go out under an event template: "credentials
    # available", and the callback-failure alert. See docs/global_email.md.
    "notification": "1077013850031295817",
    # Organisation membership invitation.
    "organisation_invitation": "1077013850031295818",
    # Registered as "WASA cert renewal": the warning sent 30, 15 and 7 days
    # before a dated outcome lapses. WASA certification is the only outcome
    # the ABDM programme dates, so this is that certificate's renewal notice.
    "certificate_expiry": "1077013850031295822",
}
