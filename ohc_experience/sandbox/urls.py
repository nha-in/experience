from django.urls import path
from django.urls import re_path

from . import views

app_name = "sandbox"
urlpatterns = [
    path("portal/", views.dashboard, name="home"),
    path("portal/products/", views.products, name="products"),
    path("onboarding/organisation/", views.organisation, name="organisation"),
    path("onboarding/product/", views.product_create, name="product-create"),
    path("portal/queries/", views.pending_queries, name="pending-queries"),
    re_path(
        r"^products/(?P<sandbox_id>SBX-\d{4}-\d+)/$",
        views.overview,
        name="overview",
    ),
    re_path(
        r"^products/(?P<sandbox_id>SBX-\d{4}-\d+)/edit/$",
        views.product_edit,
        name="product-edit",
    ),
    re_path(
        r"^products/(?P<sandbox_id>SBX-\d{4}-\d+)/credentials/$",
        views.credentials,
        name="credentials",
    ),
    re_path(
        r"^products/(?P<sandbox_id>SBX-\d{4}-\d+)/tracks/(?P<track_code>[\w-]+)/$",
        views.track,
        name="track",
    ),
    path("portal/reviews/<int:pk>/withdraw/", views.withdraw, name="withdraw"),
    path("portal/queries/<int:pk>/", views.query_action, name="query-action"),
    path("portal/attachments/<int:pk>/", views.attachment, name="attachment"),
    path(
        "portal/reviews/<int:pk>/submissions/<int:submission_id>/",
        views.submission,
        name="submission",
    ),
    path("assess/dashboard/", views.assess_dashboard, name="assess-dashboard"),
    path("assess/queue/", views.queue, name="queue"),
    path("assess/review/<int:pk>/", views.review, name="review"),
    path("portal/events/", views.events, name="events"),
    path("portal/support/", views.support, name="support"),
    path("portal/support/<str:reference>/", views.ticket, name="ticket"),
    path(
        "portal/support-files/<int:pk>/",
        views.ticket_attachment,
        name="ticket-attachment",
    ),
]
