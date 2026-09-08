from django.urls import path

from .views import user_profile_view
from .views import user_redirect_view

app_name = "users"
urlpatterns = [
    path("~redirect/", view=user_redirect_view, name="redirect"),
    path("settings/profile/", view=user_profile_view, name="profile"),
]
