from allauth.account.internal.flows.phone_verification import (
    PhoneVerificationStageProcess,
)
from allauth.account.stages import PhoneVerificationStage
from django.shortcuts import redirect

PENDING_MOBILE_NUMBER_SESSION_KEY = "signup_mobile_number"


class SignupPhoneVerificationStage(PhoneVerificationStage):
    """Texts a code to the mobile number given at signup. The number is saved
    only once that code is confirmed."""

    def handle(self):
        pending = self.request.session.pop(PENDING_MOBILE_NUMBER_SESSION_KEY, None)
        user = self.login.user
        if not pending or user is None or pending["user_id"] != user.pk:
            return None, True
        PhoneVerificationStageProcess.initiate(stage=self, phone=pending["phone"])
        return redirect("account_verify_phone"), True
