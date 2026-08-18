from django.conf import settings

from .permissions import is_ohc_team


def allauth_settings(request):
    """Expose some settings from django-allauth in templates."""
    return {
        "ACCOUNT_ALLOW_REGISTRATION": settings.ACCOUNT_ALLOW_REGISTRATION,
    }


def ohc_team(request):
    """Whether the signed-in user works for OHC.

    The app shell reads this to offer the console link, so it has to be
    available on every page rather than passed view by view.
    """
    return {"is_ohc_team": is_ohc_team(getattr(request, "user", None))}
