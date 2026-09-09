from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include
from django.urls import path
from django.views import defaults as default_views
from django.views.generic import RedirectView

from ohc_experience.users.views import user_signup_view

urlpatterns = [
    # Landing page, about, dashboard
    path("", include("ohc_experience.pages.urls")),
    # Django Admin, use {% url 'admin:index' %}
    path(settings.ADMIN_URL, admin.site.urls),
    # User management
    path("", include("ohc_experience.users.urls", namespace="users")),
    # Organisation, team and invitations
    path("", include("ohc_experience.organisations.urls", namespace="organisations")),
    # The OHC team's console — gated to staff, scoped to no organisation.
    path("ohc/", include("ohc_experience.ohc.urls", namespace="ohc")),
    # The ABDM sandbox portal: products, credentials, tracks and file downloads.
    path("", include("ohc_experience.abdm.urls", namespace="products")),
    # The NHA reviewers' screens: dashboard, queue, review detail.
    path("assess/", include("ohc_experience.abdm.assess_urls", namespace="assess")),
    # Signup is ours so an invite token can shape the form; the rest is allauth's.
    path("accounts/signup/", user_signup_view, name="account_signup"),
    # The design doc's short route for the same screen.
    path("signup/", RedirectView.as_view(pattern_name="account_signup"), name="signup"),
    path("accounts/", include("allauth.urls")),
    # Your stuff: custom urls includes go here
    path("events/", include("ohc_experience.events.urls", namespace="events")),
    path("support/", include("ohc_experience.support.urls", namespace="support")),
    path(
        "applications/",
        include("ohc_experience.experiences.urls", namespace="experiences"),
    ),
    # ...
    # Media files
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
]

if settings.DEBUG:
    # This allows the error pages to be debugged during development, just visit
    # these url in browser to see how these error pages look like.
    urlpatterns += [
        path(
            "400/",
            default_views.bad_request,
            kwargs={"exception": Exception("Bad Request!")},
        ),
        path(
            "403/",
            default_views.permission_denied,
            kwargs={"exception": Exception("Permission Denied")},
        ),
        path(
            "404/",
            default_views.page_not_found,
            kwargs={"exception": Exception("Page not Found")},
        ),
        path("500/", default_views.server_error),
    ]
    if "debug_toolbar" in settings.INSTALLED_APPS:
        import debug_toolbar

        urlpatterns = [
            path("__debug__/", include(debug_toolbar.urls)),
            *urlpatterns,
        ]
