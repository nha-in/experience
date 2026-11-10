from allauth.account.internal.flows.password_reset_by_code import (
    PASSWORD_RESET_VERIFICATION_SESSION_KEY,
)
from allauth.account.signals import email_confirmed
from django.dispatch import receiver


@receiver(email_confirmed)
def distrust_the_number_when_a_reset_confirms_the_address(
    request,
    email_address,
    **kwargs,
):
    """Signup confirms its own address. A password reset confirming it means
    someone else may have started the account, with a number of their own, so
    the owner is asked for the number at their first sign-in."""
    if PASSWORD_RESET_VERIFICATION_SESSION_KEY not in request.session:
        return
    user = email_address.user
    if user.phone_verified:
        user.phone_verified = False
        user.save(update_fields=["phone_verified"])
