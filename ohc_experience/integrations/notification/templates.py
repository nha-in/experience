"""Every notification this portal sends, and the template ABDM approved for it.

Add a notification by adding an entry here and the body template it names. Copy
the wording verbatim from `/internal/v3/notification/template/id/{id}` on
notification-db, writing its `{#var#}` placeholders as `{{ code }}`: SMS
carriers reject any text that differs from the registered template.
"""

from ohc_experience.integrations.ports import NotificationChannel
from ohc_experience.integrations.ports import NotificationContentType
from ohc_experience.integrations.ports import NotificationTemplate

EMAIL_VERIFICATION_CODE = NotificationTemplate(
    id="100001",
    channel=NotificationChannel.EMAIL,
    subject="Email verification",
    values=("code",),
    body="notifications/email_verification_code.txt",
    content_type=NotificationContentType.OTP,
)

MOBILE_VERIFICATION_CODE = NotificationTemplate(
    id="1007172534306341299",
    channel=NotificationChannel.SMS,
    subject="Mobile verification",
    values=("code",),
    body="notifications/mobile_verification_code.txt",
    content_type=NotificationContentType.OTP,
)
