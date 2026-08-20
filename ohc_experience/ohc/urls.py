from django.urls import path

from . import views

app_name = "ohc"
urlpatterns = [
    path("", views.TicketQueueView.as_view(), name="queue"),
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
