from django.urls import path

from . import views

app_name = "experiences"

urlpatterns = [
    path("", views.VendorApplicationListView.as_view(), name="list"),
    path(
        "start/<str:application_type>/",
        views.StartApplicationView.as_view(),
        name="start",
    ),
    path(
        "<str:reference>/",
        views.VendorApplicationDetailView.as_view(),
        name="detail",
    ),
    path(
        "<str:reference>/forms/<str:form_key>/",
        views.VendorFormWorkspaceView.as_view(),
        name="form",
    ),
    path(
        "<str:reference>/forms/<str:form_key>/actions/<str:form_action_key>/",
        views.VendorFormActionWorkspaceView.as_view(),
        name="form-action",
    ),
    path(
        "<str:reference>/actions/<str:action_key>/",
        views.VendorActionWorkspaceView.as_view(),
        name="action",
    ),
    path(
        "<str:reference>/queries/<int:query_pk>/",
        views.VendorQueryWorkspaceView.as_view(),
        name="query",
    ),
    path(
        "<str:reference>/access/",
        views.VendorAccessWorkspaceView.as_view(),
        name="access",
    ),
    path(
        "<str:reference>/access/<int:user_pk>/remove/",
        views.VendorRemoveAccessView.as_view(),
        name="access-remove",
    ),
    path(
        "<str:reference>/attachments/<int:attachment_pk>/",
        views.AttachmentDownloadView.as_view(),
        name="attachment",
    ),
]
