from django.urls import path

from . import views

app_name = "support"
urlpatterns = [
    path("", views.TicketListView.as_view(), name="list"),
    path("new/", views.TicketCreateView.as_view(), name="create"),
    path("<str:reference>/", views.TicketDetailView.as_view(), name="detail"),
    path("<str:reference>/reply/", views.TicketReplyView.as_view(), name="reply"),
    path("<str:reference>/status/", views.TicketStatusView.as_view(), name="status"),
]
