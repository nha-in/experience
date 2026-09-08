import json
import secrets
import time
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen

from django import forms
from django.conf import settings

CHALLENGE_TTL = 300


class SignupVerificationMixin:
    def __init__(self, *args, request=None, **kwargs):
        self.signup_request = request
        super().__init__(*args, **kwargs)
        self.turnstile_site_key = settings.TURNSTILE_SITE_KEY
        if not settings.SANDBOX_SIGNUP_CAPTCHA:
            return
        if self.turnstile_site_key:
            self.fields["captcha"] = forms.CharField(
                max_length=2048,
                widget=forms.HiddenInput,
            )
        elif settings.DEBUG and request:
            challenge = request.session.get("signup_challenge")
            if not challenge or time.time() - challenge[2] > CHALLENGE_TTL:
                challenge = [
                    secrets.randbelow(8) + 2,
                    secrets.randbelow(8) + 2,
                    time.time(),
                ]
                request.session["signup_challenge"] = challenge
            self.fields["captcha"] = forms.IntegerField(
                label=f"Verification: {challenge[0]} + {challenge[1]} = ?",
            )
        else:
            self.fields["captcha"] = forms.CharField(
                label="Verification",
                widget=forms.HiddenInput,
            )

    def clean_captcha(self):
        value = self.cleaned_data["captcha"]
        request = self.signup_request
        if self.turnstile_site_key and settings.TURNSTILE_SECRET_KEY and request:
            payload = urlencode(
                {"secret": settings.TURNSTILE_SECRET_KEY, "response": value},
            ).encode()
            verification = Request(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data=payload,
                method="POST",
            )
            try:
                with urlopen(verification, timeout=5) as response:  # noqa: S310
                    result = json.load(response)
                if (
                    result.get("success")
                    and result.get("hostname") == request.get_host().split(":", 1)[0]
                ):
                    return value
            except OSError, ValueError:
                pass
        elif settings.DEBUG and request and not self.turnstile_site_key:
            challenge = request.session.get("signup_challenge")
            if (
                challenge
                and time.time() - challenge[2] <= CHALLENGE_TTL
                and value == challenge[0] + challenge[1]
            ):
                return value
        msg = "Verification failed. Refresh the page and try again."
        raise forms.ValidationError(msg)
