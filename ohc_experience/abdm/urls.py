from django.urls import path

from . import views

app_name = "products"

urlpatterns = [
    path(
        "onboarding/product/",
        views.ProductOnboardingView.as_view(),
        name="onboarding-product",
    ),
    path("products/new/", views.ProductCreateView.as_view(), name="new"),
    path(
        "products/<str:sandbox_id>/",
        views.ProductOverviewView.as_view(),
        name="overview",
    ),
    path(
        "products/<str:sandbox_id>/edit/",
        views.ProductEditView.as_view(),
        name="edit",
    ),
    path(
        "products/<str:sandbox_id>/credentials/",
        views.CredentialsView.as_view(),
        name="credentials",
    ),
    path(
        "products/<str:sandbox_id>/credentials/reveal/",
        views.CredentialRevealView.as_view(),
        name="credentials-reveal",
    ),
    path(
        "products/<str:sandbox_id>/credentials/rotate/",
        views.CredentialRotateView.as_view(),
        name="credentials-rotate",
    ),
    path(
        "products/<str:sandbox_id>/credentials/revoke/",
        views.CredentialRevokeView.as_view(),
        name="credentials-revoke",
    ),
    path(
        "products/<str:sandbox_id>/credentials/request/",
        views.CredentialRequestView.as_view(),
        name="credentials-request",
    ),
    path(
        "products/<str:sandbox_id>/credentials/urls/",
        views.CallbackUrlsView.as_view(),
        name="credentials-urls",
    ),
    path(
        "products/<str:sandbox_id>/credentials/test/",
        views.CallbackTestView.as_view(),
        name="credentials-test",
    ),
    path(
        "products/<str:sandbox_id>/tracks/<str:track>/",
        views.TrackView.as_view(),
        name="track",
    ),
    path(
        "products/<str:sandbox_id>/tracks/<str:track>/<str:milestone>/save/",
        views.MilestoneSaveView.as_view(),
        name="milestone-save",
    ),
    path(
        "products/<str:sandbox_id>/tracks/<str:track>/<str:milestone>/withdraw/",
        views.MilestoneWithdrawView.as_view(),
        name="milestone-withdraw",
    ),
    path(
        "files/<slug:kind>/<int:pk>/<slug:field>/",
        views.DocumentDownloadView.as_view(),
        name="document",
    ),
    path(
        "queries/<int:pk>/reply/",
        views.QueryReplyView.as_view(),
        name="query-reply",
    ),
]
