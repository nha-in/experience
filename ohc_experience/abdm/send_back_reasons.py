"""Reasons a reviewer chooses from when sending a request back.

Compressed from every remark the old sandbox portal's reviewers wrote; the
mapping from each old remark to these is kept in reasons-compressed.xlsx.
The engine offers Other after these, with the reviewer's own words.
"""

ORGANISATION_SEND_BACK_REASONS = (
    "Intent unclear",
    "Incomplete application",
    "Incorrect details in the application",
    "Duplicate application",
    "Wrong solution type",
    "Wrong type of entity",
    "Wrong category",
    "Use a corporate email address",
    "Use an official email address",
    "Use a government email address",
    "Wrong website address",
    "Website unreachable or not working",
    "Website under construction",
    "Website domain is for sale",
    "Not enough information on the website",
    "Website information is incorrect or does not match the application",
    "Register on NHPR, NHRR or HFR instead",
    "Test or junk entry",
)

EXIT_SEND_BACK_REASONS = (
    "Incorrect document",
    "Incomplete documentation",
    "FT report missing",
    "WASA report missing",
    "FT not done by an empaneled agency",
    "WASA not done by an empaneled agency",
    "Incomplete integration",
    "Exit requested without NHA review or approval",
    "Internal demo not cleared",
    "HTC demo not cleared",
    "Milestone details missing or incorrect",
    "Duplicate exit request",
    "Filed under the wrong application or track",
)
