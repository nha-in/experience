from django.urls import path

from . import event_views
from . import production_views
from . import staff_views
from . import views

app_name = "experiences"
urlpatterns = [
    path("portal/", views.dashboard, name="home"),
    path("portal/staff/", staff_views.staff_list, name="staff-list"),
    path("portal/staff/new/", staff_views.staff_edit, name="staff-create"),
    path("portal/staff/<int:pk>/", staff_views.staff_edit, name="staff-edit"),
    path(
        "portal/staff/<int:pk>/archive/",
        staff_views.staff_archive,
        name="staff-archive",
    ),
    path("portal/products/", views.products, name="products"),
    path(
        "portal/products/<str:reference>/",
        views.product_detail,
        name="product-detail",
    ),
    path("portal/organizations/", views.organizations, name="organizations"),
    path(
        "portal/organizations/<slug:slug>/",
        views.organization_detail,
        name="organization-detail",
    ),
    path("onboarding/organisation/", views.organisation, name="organisation"),
    path("onboarding/product/", views.product_create, name="product-create"),
    path("portal/queries/", views.pending_queries, name="pending-queries"),
    path(
        "products/<str:reference>/",
        views.overview,
        name="overview",
    ),
    path(
        "products/<str:reference>/edit/",
        views.product_edit,
        name="product-edit",
    ),
    path(
        "products/<str:reference>/credentials/",
        views.credentials,
        name="credentials",
    ),
    path(
        "products/<str:reference>/reference-environment/",
        views.reference_environment,
        name="reference-environment",
    ),
    path(
        "products/<str:reference>/agent-skills/",
        views.agent_skills,
        name="agent-skills",
    ),
    path(
        "products/<str:reference>/handoffs/<slug:handoff_key>/",
        views.product_handoff,
        name="product-handoff",
    ),
    path(
        "products/<str:reference>/certification/",
        views.product_certification,
        name="product-certification",
    ),
    path(
        "products/<str:reference>/tracks/<str:track_code>/",
        views.track,
        name="track",
    ),
    path("portal/reviews/<int:pk>/withdraw/", views.withdraw, name="withdraw"),
    path("portal/queries/<int:pk>/", views.query_action, name="query-action"),
    path("portal/attachments/<int:pk>/", views.attachment, name="attachment"),
    path(
        "portal/attachments/<int:pk>/preview/<path:filename>",
        views.attachment,
        name="attachment-preview",
    ),
    path(
        "portal/reviews/<int:pk>/submissions/<int:submission_id>/",
        views.submission,
        name="submission",
    ),
    path("assess/dashboard/", views.assess_dashboard, name="assess-dashboard"),
    path("assess/queue/", views.queue, name="queue"),
    path("assess/review/<int:pk>/", views.review, name="review"),
    path(
        "assess/production/",
        production_views.production_list,
        name="production-list",
    ),
    # Before the detail pattern, which would otherwise take "export" as a reference.
    path(
        "assess/production/export/",
        production_views.production_export,
        name="production-export",
    ),
    path(
        "assess/production/<str:reference>/",
        production_views.production_detail,
        name="production-detail",
    ),
    path("portal/reviews/<int:pk>/open/", views.open_record, name="review-open"),
    path("portal/events/", views.events, name="events"),
    path("portal/events/manage/", event_views.event_manage, name="event-manage"),
    path("portal/events/new/", event_views.event_edit, name="event-create"),
    path("portal/events/<int:pk>/edit/", event_views.event_edit, name="event-edit"),
    path(
        "portal/events/<int:pk>/participants/",
        event_views.event_participants,
        name="event-participants",
    ),
    path(
        "portal/events/<int:pk>/publication/",
        event_views.event_publication,
        name="event-publication",
    ),
    path(
        "portal/events/<int:pk>/delete/",
        event_views.event_delete,
        name="event-delete",
    ),
    path("portal/support/", views.support, name="support"),
    path("portal/support/<str:reference>/", views.ticket, name="ticket"),
    path(
        "portal/support-files/<int:pk>/",
        views.ticket_attachment,
        name="ticket-attachment",
    ),
    path(
        "portal/support-files/<int:pk>/preview/<path:filename>",
        views.ticket_attachment,
        name="ticket-attachment-preview",
    ),
]
