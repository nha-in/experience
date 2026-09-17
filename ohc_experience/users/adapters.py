from __future__ import annotations

import logging
import typing

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from ohc_experience.core.mail import apply_gateway_template
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import NotificationChannel
from ohc_experience.integrations.ports import NotificationContentType
from ohc_experience.integrations.ports import NotificationMessage
from ohc_experience.integrations.registry import get_notification_gateway
from ohc_experience.users.fields import INDIA
from ohc_experience.users.fields import MobileNumberField

if typing.TYPE_CHECKING:
    from allauth.socialaccount.models import SocialLogin
    from django.http import HttpRequest

    from ohc_experience.users.models import User

logger = logging.getLogger(__name__)

PHONE_STAGE = "allauth.account.stages.PhoneVerificationStage"
EMAIL_STAGE = "allauth.account.stages.EmailVerificationStage"
SIGNUP_PHONE_STAGE = "ohc_experience.users.stages.SignupPhoneVerificationStage"

CODE_SENT_MESSAGES = frozenset(
    {
        "account/messages/email_confirmation_sent.txt",
        "account/messages/phone_verification_sent.txt",
    },
)
CODE_UNDELIVERED = "verification_code_undelivered"


class AccountAdapter(DefaultAccountAdapter):
    def render_mail(self, template_prefix, email, context, headers=None):
        message = super().render_mail(template_prefix, email, context, headers)
        apply_gateway_template(message, template_prefix)
        return message

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def get_login_stages(self) -> list[str]:
        stages = [stage for stage in super().get_login_stages() if stage != PHONE_STAGE]
        stages.insert(stages.index(EMAIL_STAGE) + 1, SIGNUP_PHONE_STAGE)
        return stages

    def send_confirmation_mail(self, request, emailconfirmation, signup) -> None:
        self._send_code(
            request,
            NotificationChannel.EMAIL,
            emailconfirmation.email_address.email,
            emailconfirmation.key,
            template_id=settings.NOTIFICATION_EMAIL_OTP_TEMPLATE_ID,
            subject="Email verification",
        )

    def send_verification_code_sms(self, user, phone, code, **kwargs) -> None:
        self._send_code(
            self.request,
            NotificationChannel.SMS,
            phone.removeprefix(INDIA),
            code,
            template_id=settings.NOTIFICATION_SMS_OTP_TEMPLATE_ID,
            subject="Mobile verification",
        )

    def add_message(self, request, level, message_template=None, *args, **kwargs):
        if message_template in CODE_SENT_MESSAGES and getattr(
            request,
            CODE_UNDELIVERED,
            False,
        ):
            return
        super().add_message(request, level, message_template, *args, **kwargs)

    def phone_form_field(self, **kwargs) -> MobileNumberField:
        return MobileNumberField(**kwargs)

    def get_phone(self, user: User) -> tuple[str, bool] | None:
        if not user.phone_number:
            return None
        return user.phone_number, user.phone_verified

    def set_phone(self, user: User, phone: str, verified: bool) -> None:  # noqa: FBT001
        # Unverified numbers are never stored. allauth keeps a number in its
        # verification process until the code is confirmed.
        if verified:
            self.set_phone_verified(user, phone)

    def set_phone_verified(self, user: User, phone: str) -> None:
        user.phone_number = phone
        user.phone_verified = True
        user.save(update_fields=["phone_number", "phone_verified"])

    def get_user_by_phone(self, phone: str) -> User | None:
        return (
            get_user_model()
            .objects.filter(phone_number=phone, phone_verified=True)
            .first()
        )

    def _send_code(  # noqa: PLR0913
        self,
        request: HttpRequest,
        channel: NotificationChannel,
        receiver: str,
        code: str,
        *,
        template_id: str,
        subject: str,
    ) -> None:
        message = NotificationMessage(
            channel=channel,
            receiver=receiver,
            template_id=template_id,
            subject=subject,
            values=(code,),
            content_type=NotificationContentType.OTP,
        )
        try:
            get_notification_gateway().send(message)
        except AdapterError:
            logger.exception("Verification code was not sent by %s", channel)
            setattr(request, CODE_UNDELIVERED, True)
            messages.error(
                request,
                _("We could not send your code. Please send a new one shortly."),
            )


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(
        self,
        request: HttpRequest,
        sociallogin: SocialLogin,
    ) -> bool:
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def populate_user(
        self,
        request: HttpRequest,
        sociallogin: SocialLogin,
        data: dict[str, typing.Any],
    ) -> User:
        """
        Populates user information from social provider info.

        See: https://docs.allauth.org/en/latest/socialaccount/advanced.html#creating-and-populating-user-instances
        """
        user = super().populate_user(request, sociallogin, data)
        if not user.name:
            if name := data.get("name"):
                user.name = name
            elif first_name := data.get("first_name"):
                user.name = first_name
                if last_name := data.get("last_name"):
                    user.name += f" {last_name}"
        return user
