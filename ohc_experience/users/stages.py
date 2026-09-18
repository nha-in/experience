from __future__ import annotations

from allauth.account.adapter import get_adapter
from allauth.account.internal.flows.phone_verification import (
    PhoneVerificationStageProcess,
)
from allauth.account.stages import EmailVerificationStage
from django.shortcuts import redirect


class VerificationStage(EmailVerificationStage):
    """Sends both codes, then holds the login at the screen that takes them.

    allauth verifies an address and a number in consecutive stages, each with
    its own page. Signup asks for both, so this stage starts both verification
    processes and hands over to the screen that shows them together. With
    nothing but the address left to confirm, only the email half is pending.
    """

    key = "verify_account"
    urlname = "account_verification"

    def handle(self):
        email_response, keep_going = super().handle()
        phone = self._unverified_phone()
        if phone is not None:
            PhoneVerificationStageProcess.initiate(stage=self, phone=phone)
        elif email_response is None:
            return None, keep_going
        return redirect(self.urlname), keep_going

    def _unverified_phone(self) -> str | None:
        """Signup keeps its number on the account unverified, so every sign-in
        asks for its code until one is confirmed. An NHA team account is made
        for someone by a superuser, number included, so it is not asked.
        """
        user = self.login.user
        if user is None or user.is_nha_team:
            return None
        phone_verified = get_adapter().get_phone(user)
        if phone_verified is None or phone_verified[1]:
            return None
        return phone_verified[0]
