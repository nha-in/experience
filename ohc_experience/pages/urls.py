from django.urls import path

from ohc_experience.sandbox.views import dashboard

from .views import LandingView

urlpatterns = [
    path("", LandingView.as_view(), name="home"),
    path("dashboard/", dashboard, name="dashboard"),
]
