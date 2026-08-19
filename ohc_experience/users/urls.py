from django.urls import path

from .views import legacy_update_redirect
from .views import user_detail_view
from .views import user_profile_view
from .views import user_redirect_view

app_name = "users"
urlpatterns = [
    path("~redirect/", view=user_redirect_view, name="redirect"),
    path("settings/profile/", view=user_profile_view, name="profile"),
    path("~update/", view=legacy_update_redirect, name="update"),
    path("users/<int:pk>/", view=user_detail_view, name="detail"),
]
