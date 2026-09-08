from django.urls import path

from . import assess_views as views

app_name = "assess"

urlpatterns = [
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("queue/", views.QueueView.as_view(), name="queue"),
    path("review/<str:reference>/", views.ReviewDetailView.as_view(), name="review"),
    path(
        "review/<str:reference>/start/",
        views.ReviewStartView.as_view(),
        name="review-start",
    ),
    path(
        "review/<str:reference>/assign/",
        views.ReviewAssignView.as_view(),
        name="review-assign",
    ),
    path(
        "review/<str:reference>/query/",
        views.ReviewQueryView.as_view(),
        name="review-query",
    ),
    path(
        "review/<str:reference>/approve/",
        views.ReviewApproveView.as_view(),
        name="review-approve",
    ),
    path(
        "review/<str:reference>/send-back/",
        views.ReviewSendBackView.as_view(),
        name="review-send-back",
    ),
    path(
        "review/<str:reference>/queries/<int:pk>/resolve/",
        views.QueryResolveView.as_view(),
        name="query-resolve",
    ),
]
