from django.urls import path

from . import views

app_name = "events"
urlpatterns = [
    path("", views.EventListView.as_view(), name="list"),
    path("<slug:slug>/", views.EventDetailView.as_view(), name="detail"),
    path(
        "<slug:slug>/<slug:action>/",
        views.RegistrationView.as_view(),
        name="registration",
    ),
]
