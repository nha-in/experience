from django.urls import path

from . import views

app_name = "products"

urlpatterns = [
    path("", views.ProductListView.as_view(), name="list"),
    path("new/", views.ProductCreateView.as_view(), name="create"),
    path("<slug:slug>/", views.ProductDetailView.as_view(), name="detail"),
    path("<slug:slug>/edit/", views.ProductUpdateView.as_view(), name="edit"),
    path(
        "<slug:slug>/applications/start/<str:application_type>/",
        views.StartApplicationView.as_view(),
        name="start-application",
    ),
]
