"""Every notification this portal sends, and the template ABDM approved for it.

Add a notification by adding an entry here: nothing else needs a new setting.
The wording lives with the notification team — the gateway fetches the approved
text by id and fills only its `{0}`, `{1}`… placeholders.
"""

from ohc_experience.integrations.ports import NotificationChannel
from ohc_experience.integrations.ports import NotificationContentType
from ohc_experience.integrations.ports import NotificationTemplate

EMAIL_VERIFICATION_CODE = NotificationTemplate(
    id="1007164181681962329",
    channel=NotificationChannel.EMAIL,
    subject="Email verification",
    values=("code",),
    content_type=NotificationContentType.OTP,
)

MOBILE_VERIFICATION_CODE = NotificationTemplate(
    id="1007164181681962323",
    channel=NotificationChannel.SMS,
    subject="Mobile verification",
    values=("code",),
    content_type=NotificationContentType.OTP,
)
