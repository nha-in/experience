"""The htmx idiom reference that shipped with the htmx setup.

Kept out of the product routes but wired at /htmx-demo/ so the four worked
examples — polling, a session counter, a debounced search and a form POST —
stay runnable for whoever writes the next htmx screen.
"""

from __future__ import annotations

from django.urls import path

from ohc_experience.pages import demo_views as views

urlpatterns = [
    path("", views.HomeView.as_view(), name="htmx-demo"),
    path("htmx/time/", views.server_time, name="htmx-time"),
    path("htmx/counter/", views.counter, name="htmx-counter"),
    path("htmx/search/", views.search, name="htmx-search"),
    path("htmx/greet/", views.greet, name="htmx-greet"),
]
