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
from ohc_experience.integrations.notification.templates import EMAIL_VERIFICATION_CODE
from ohc_experience.integrations.notification.templates import MOBILE_VERIFICATION_CODE
from ohc_experience.integrations.notification.templates import PASSWORD_RESET_CODE
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import NotificationMessage
from ohc_experience.integrations.registry import get_notification_gateway
from ohc_experience.users.fields import INDIA
from ohc_experience.users.fields import MobileNumberField

if typing.TYPE_CHECKING:
    from allauth.socialaccount.models import SocialLogin
    from django.http import HttpRequest

    from ohc_experience.integrations.ports import NotificationTemplate
    from ohc_experience.users.models import User

logger = logging.getLogger(__name__)

PHONE_STAGE = "allauth.account.stages.PhoneVerificationStage"
EMAIL_STAGE = "allauth.account.stages.EmailVerificationStage"
VERIFICATION_STAGE = "ohc_experience.users.stages.VerificationStage"

CODE_SENT_MESSAGES = frozenset(
    {
        "account/messages/email_confirmation_sent.txt",
        "account/messages/phone_verification_sent.txt",
    },
)
CODE_UNDELIVERED = "verification_code_undelivered"
PASSWORD_RESET_CODE_MAIL = "account/email/password_reset_code"  # noqa: S105


class AccountAdapter(DefaultAccountAdapter):
    def render_mail(self, template_prefix, email, context, headers=None):
        message = super().render_mail(template_prefix, email, context, headers)
        apply_gateway_template(message, template_prefix)
        return message

    def send_mail(self, template_prefix, email, context) -> None:
        """The reset code is a code: it takes the same route as the others,
        reaching the person while they wait rather than through the outbox."""
        if template_prefix == PASSWORD_RESET_CODE_MAIL:
            self._send_code(
                context.get("request") or self.request,
                PASSWORD_RESET_CODE,
                email,
                context["code"],
            )
            return
        super().send_mail(template_prefix, email, context)

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def get_login_stages(self) -> list[str]:
        """One stage takes both codes, in place of allauth's two."""
        stages = super().get_login_stages()
        stages[stages.index(EMAIL_STAGE)] = VERIFICATION_STAGE
        return [stage for stage in stages if stage != PHONE_STAGE]

    def send_confirmation_mail(self, request, emailconfirmation, signup) -> None:
        self._send_code(
            request,
            EMAIL_VERIFICATION_CODE,
            emailconfirmation.email_address.email,
            emailconfirmation.key,
        )

    def send_verification_code_sms(self, user, phone, code, **kwargs) -> None:
        self._send_code(
            self.request,
            MOBILE_VERIFICATION_CODE,
            phone.removeprefix(INDIA),
            code,
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
        if verified:
            self.set_phone_verified(user, phone)
        elif not user.phone_verified:
            # Keep an unconfirmed number so a later sign-in can ask for its
            # code again, but never let one displace a confirmed number.
            user.phone_number = phone
            user.save(update_fields=["phone_number"])

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

    def _send_code(
        self,
        request: HttpRequest,
        template: NotificationTemplate,
        receiver: str,
        code: str,
    ) -> None:
        message = NotificationMessage(
            template=template,
            receiver=receiver,
            values=(code,),
        )
        try:
            get_notification_gateway().send(message)
        except AdapterError:
            logger.exception(
                "Verification code was not sent by %s",
                template.channel,
            )
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
