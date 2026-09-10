from django.conf import settings

from .permissions import is_nha_team


def allauth_settings(request):
    """Expose some settings from django-allauth in templates."""
    return {
        "ACCOUNT_ALLOW_REGISTRATION": settings.ACCOUNT_ALLOW_REGISTRATION,
    }


def nha_team(request):
    """Whether the signed-in user works for NHA.

    The app shell reads this to offer the console link, so it has to be
    available on every page rather than passed view by view.
    """
    return {"is_nha_team": is_nha_team(getattr(request, "user", None))}
