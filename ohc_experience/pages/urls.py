from django.urls import path
from django.views.generic import TemplateView

from .views import DashboardView
from .views import LandingView

urlpatterns = [
    path("", LandingView.as_view(), name="home"),
    path(
        "about/",
        TemplateView.as_view(template_name="pages/about.html"),
        name="about",
    ),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
]
