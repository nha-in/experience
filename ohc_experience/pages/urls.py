from __future__ import annotations

from django.urls import path

from ohc_experience.pages import views

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("htmx/time/", views.server_time, name="htmx-time"),
    path("htmx/counter/", views.counter, name="htmx-counter"),
    path("htmx/search/", views.search, name="htmx-search"),
    path("htmx/greet/", views.greet, name="htmx-greet"),
]
