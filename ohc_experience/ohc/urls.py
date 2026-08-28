from django.urls import path

from ohc_experience.experiences import views as experience_views

from . import views

app_name = "ohc"
urlpatterns = [
    path("", views.TicketQueueView.as_view(), name="queue"),
    path(
        "applications/",
        experience_views.AdminApplicationListView.as_view(),
        name="applications",
    ),
    path(
        "applications/<str:reference>/",
        experience_views.AdminApplicationDetailView.as_view(),
        name="application-detail",
    ),
    path(
        "applications/<str:reference>/actions/<str:action_key>/",
        experience_views.AdminActionWorkspaceView.as_view(),
        name="application-action",
    ),
    path(
        "applications/<str:reference>/forms/<str:form_key>/actions/"
        "<str:form_action_key>/",
        experience_views.AdminFormActionWorkspaceView.as_view(),
        name="application-form-action",
    ),
    path(
        "applications/<str:reference>/queries/<int:query_pk>/",
        experience_views.AdminQueryWorkspaceView.as_view(),
        name="application-query",
    ),
    path(
        "applications/<str:reference>/queries/<int:query_pk>/resolve/",
        experience_views.AdminResolveQueryView.as_view(),
        name="application-query-resolve",
    ),
    path(
        "applications/<str:reference>/access/",
        experience_views.AdminAccessWorkspaceView.as_view(),
        name="application-access",
    ),
    path(
        "applications/<str:reference>/access/<int:user_pk>/remove/",
        experience_views.AdminRemoveAccessView.as_view(),
        name="application-access-remove",
    ),
    path(
        "tickets/<str:reference>/",
        views.TicketDetailView.as_view(),
        name="ticket",
    ),
    path(
        "tickets/<str:reference>/reply/",
        views.TicketReplyView.as_view(),
        name="ticket-reply",
    ),
    path(
        "tickets/<str:reference>/update/",
        views.TicketUpdateView.as_view(),
        name="ticket-update",
    ),
    path(
        "organisations/",
        views.OrganisationListView.as_view(),
        name="organisations",
    ),
    path(
        "organisations/<slug:slug>/",
        views.OrganisationDetailView.as_view(),
        name="organisation",
    ),
    path(
        "organisations/<slug:slug>/verification/",
        views.OrganisationVerificationView.as_view(),
        name="organisation-verification",
    ),
    path("events/", views.EventListView.as_view(), name="events"),
    path("sandboxes/", views.SandboxQueueView.as_view(), name="sandboxes"),
    path(
        "sandboxes/<int:pk>/provision/",
        views.SandboxProvisionView.as_view(),
        name="sandbox-provision",
    ),
    path(
        "sandboxes/<int:pk>/revoke/",
        views.SandboxRevokeView.as_view(),
        name="sandbox-revoke",
    ),
    path("events/new/", views.EventCreateView.as_view(), name="event-create"),
    path(
        "events/<slug:slug>/edit/",
        views.EventUpdateView.as_view(),
        name="event-update",
    ),
    path(
        "events/<slug:slug>/publish/",
        views.EventPublishToggleView.as_view(),
        name="event-publish",
    ),
]
