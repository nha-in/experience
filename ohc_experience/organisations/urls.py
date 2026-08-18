from django.urls import path

from . import views

app_name = "organisations"
urlpatterns = [
    path("onboarding/", views.OnboardingView.as_view(), name="onboarding"),
    path(
        "settings/organisation/",
        views.OrganisationDetailView.as_view(),
        name="detail",
    ),
    path("settings/team/", views.TeamView.as_view(), name="team"),
    path(
        "settings/team/members/<int:pk>/role/",
        views.MembershipRoleUpdateView.as_view(),
        name="member-role",
    ),
    path(
        "settings/team/members/<int:pk>/remove/",
        views.MembershipRemoveView.as_view(),
        name="member-remove",
    ),
    path(
        "settings/team/invitations/<int:pk>/resend/",
        views.InvitationResendView.as_view(),
        name="invitation-resend",
    ),
    path(
        "settings/team/invitations/<int:pk>/revoke/",
        views.InvitationRevokeView.as_view(),
        name="invitation-revoke",
    ),
    path(
        "invitations/<str:token>/accept/",
        views.InvitationAcceptView.as_view(),
        name="invitation-accept",
    ),
]
