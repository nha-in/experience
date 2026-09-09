import contextlib
from http import HTTPStatus
from importlib import reload

import pytest
from django.contrib import admin
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.urls import reverse
from django.urls import reverse_lazy
from pytest_django.asserts import assertRedirects

from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.users.models import User
from ohc_experience.users.tests.factories import UserFactory


class TestUserAdmin:
    def test_changelist(self, admin_client):
        url = reverse("admin:users_user_changelist")
        response = admin_client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_search(self, admin_client):
        url = reverse("admin:users_user_changelist")
        response = admin_client.get(url, data={"q": "test"})
        assert response.status_code == HTTPStatus.OK

    def test_add(self, admin_client):
        url = reverse("admin:users_user_add")
        response = admin_client.get(url)
        assert response.status_code == HTTPStatus.OK

        response = admin_client.post(
            url,
            data={
                "email": "new-admin@example.com",
                "password1": "My_R@ndom-P@ssw0rd",
                "password2": "My_R@ndom-P@ssw0rd",
            },
        )
        assert response.status_code == HTTPStatus.FOUND
        assert User.objects.filter(email="new-admin@example.com").exists()

    def test_view_user(self, admin_client):
        user = User.objects.get(email="admin@example.com")
        url = reverse("admin:users_user_change", kwargs={"object_id": user.pk})
        response = admin_client.get(url)
        assert response.status_code == HTTPStatus.OK

    @pytest.fixture
    def _force_allauth(self, settings):
        settings.DJANGO_ADMIN_FORCE_ALLAUTH = True
        # Reload the admin module to apply the setting change
        import ohc_experience.users.admin as users_admin  # noqa: PLC0415

        with contextlib.suppress(admin.sites.AlreadyRegistered):  # type: ignore[attr-defined]
            reload(users_admin)

    @pytest.mark.django_db
    @pytest.mark.usefixtures("_force_allauth")
    def test_allauth_login(self, rf, settings):
        request = rf.get("/fake-url")
        request.user = AnonymousUser()
        response = admin.site.login(request)

        # The `admin` login view should redirect to the `allauth` login view
        target_url = reverse(settings.LOGIN_URL) + "?next=" + request.path
        assertRedirects(response, target_url, fetch_redirect_response=False)


@pytest.fixture
def ohc_member(db) -> User:
    return UserFactory.create(
        email="anand@ohc.network",
        name="Anand S",
        is_ohc_team=True,
        is_staff=True,
    )


@pytest.fixture
def vendor_member(db) -> User:
    membership = MembershipFactory.create(
        organisation__name="Arogya Systems",
        user__email="meera@arogyasystems.in",
        user__name="Meera Krishnan",
        role=Role.OWNER,
    )
    return membership.user


@pytest.fixture
def staff_but_not_superuser(db) -> User:
    """Someone who can reach the admin but may not mint OHC accounts."""
    return UserFactory.create(email="desk@ohc.network", is_staff=True)


def changelist_emails(response) -> set[str]:
    return set(response.context["cl"].queryset.values_list("email", flat=True))


class TestUserChangelist:
    def test_it_lists_both_populations(self, admin_client, ohc_member, vendor_member):
        url = reverse("admin:users_user_changelist")

        response = admin_client.get(url)

        assert response.status_code == HTTPStatus.OK
        assert {ohc_member.email, vendor_member.email} <= changelist_emails(response)

    def test_the_account_type_filter_narrows_to_the_ohc_team(
        self,
        admin_client,
        ohc_member,
        vendor_member,
    ):
        url = reverse("admin:users_user_changelist")

        response = admin_client.get(url, data={"population": "ohc"})

        assert changelist_emails(response) == {ohc_member.email}

    def test_the_account_type_filter_narrows_to_vendors(
        self,
        admin_client,
        ohc_member,
        vendor_member,
    ):
        url = reverse("admin:users_user_changelist")

        response = admin_client.get(url, data={"population": "vendor"})

        emails = changelist_emails(response)

        assert vendor_member.email in emails
        assert ohc_member.email not in emails

    def test_the_columns_name_the_population_and_the_organisations(
        self,
        ohc_member,
        vendor_member,
    ):
        user_admin = admin.site.get_model_admin(User)

        assert str(user_admin.account_type(ohc_member)) == "OHC team"
        assert str(user_admin.account_type(vendor_member)) == "Vendor"
        assert user_admin.organisation_names(vendor_member) == "Arogya Systems"
        assert user_admin.organisation_names(ohc_member) == "—"


class TestOhcTeamActions:
    def test_granting_ohc_team_access(self, admin_client, vendor_member):
        response = admin_client.post(
            reverse("admin:users_user_changelist"),
            data={
                "action": "grant_ohc_team",
                "index": "0",
                "_selected_action": [str(vendor_member.pk)],
            },
        )
        vendor_member.refresh_from_db()

        assert response.status_code == HTTPStatus.FOUND
        assert vendor_member.is_ohc_team is True

    def test_revoking_ohc_team_access(self, admin_client, ohc_member):
        response = admin_client.post(
            reverse("admin:users_user_changelist"),
            data={
                "action": "revoke_ohc_team",
                "index": "0",
                "_selected_action": [str(ohc_member.pk)],
            },
        )
        ohc_member.refresh_from_db()

        assert response.status_code == HTTPStatus.FOUND
        assert ohc_member.is_ohc_team is False

    def test_an_action_only_touches_the_selected_rows(
        self,
        admin_client,
        ohc_member,
        vendor_member,
    ):
        admin_client.post(
            reverse("admin:users_user_changelist"),
            data={
                "action": "grant_ohc_team",
                "index": "0",
                "_selected_action": [str(vendor_member.pk)],
            },
        )
        untouched = User.objects.get(email="admin@example.com")

        assert untouched.is_ohc_team is False


class TestAddOhcMember:
    url = reverse_lazy("admin:users_user_add_ohc_member")

    def test_a_superuser_sees_the_form(self, admin_client):
        response = admin_client.get(self.url)

        assert response.status_code == HTTPStatus.OK
        assert "Add OHC team member" in response.content.decode()

    def test_a_plain_staff_user_is_turned_away(self, sign_in, staff_but_not_superuser):
        client = sign_in(staff_but_not_superuser)

        response = client.get(self.url)

        assertRedirects(
            response,
            reverse("admin:users_user_changelist"),
            fetch_redirect_response=False,
        )
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert messages == ["Only superusers can add OHC team members."]

    def test_a_plain_staff_user_cannot_post_one_either(
        self,
        sign_in,
        staff_but_not_superuser,
    ):
        client = sign_in(staff_but_not_superuser)

        client.post(
            self.url,
            data={
                "email": "sneaky@ohc.network",
                "name": "Sneaky",
                "password1": "My_R@ndom-P@ssw0rd",
                "password2": "My_R@ndom-P@ssw0rd",
            },
        )

        assert not User.objects.filter(email="sneaky@ohc.network").exists()

    def test_posting_it_creates_an_ohc_team_account(self, admin_client):
        response = admin_client.post(
            self.url,
            data={
                "email": "anand@ohc.network",
                "name": "Anand S",
                "password1": "My_R@ndom-P@ssw0rd",
                "password2": "My_R@ndom-P@ssw0rd",
            },
        )

        created = User.objects.get(email="anand@ohc.network")

        assert response.status_code == HTTPStatus.FOUND
        assert response.url == reverse(
            "admin:users_user_change",
            args=[created.pk],
        )
        assert created.name == "Anand S"
        assert created.is_ohc_team is True
        assert created.is_staff is True
        assert created.is_superuser is False
        assert created.check_password("My_R@ndom-P@ssw0rd")

    def test_a_duplicate_address_is_rejected(self, admin_client, ohc_member):
        response = admin_client.post(
            self.url,
            data={
                "email": ohc_member.email,
                "name": "Someone else",
                "password1": "My_R@ndom-P@ssw0rd",
                "password2": "My_R@ndom-P@ssw0rd",
            },
        )

        assert response.status_code == HTTPStatus.OK
        assert "This email has already been taken." in response.content.decode()
        assert User.objects.filter(email=ohc_member.email).count() == 1


class TestVendorsAreLockedOutOfTheAdmin:
    """A vendor is not staff, so every OHC-only admin route bounces them.

    The console gate (OhcTeamRequiredMixin) is asserted in ohc/tests/test_views.py;
    this is the other half of the same boundary — the admin screens that mint and
    revoke OHC team access. A vendor reaching either of them would be able to
    grant themselves the entire support queue.
    """

    def test_a_vendor_cannot_open_the_add_ohc_member_form(
        self,
        sign_in,
        vendor_member,
    ):
        client = sign_in(vendor_member)

        response = client.get(reverse("admin:users_user_add_ohc_member"))

        assert response.status_code == HTTPStatus.FOUND
        assert response.url.startswith(reverse("admin:login"))

    def test_a_vendor_post_creates_no_ohc_account(self, sign_in, vendor_member):
        client = sign_in(vendor_member)

        response = client.post(
            reverse("admin:users_user_add_ohc_member"),
            data={
                "email": "sneaky@arogyasystems.in",
                "name": "Sneaky",
                "password1": "My_R@ndom-P@ssw0rd",
                "password2": "My_R@ndom-P@ssw0rd",
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        assert not User.objects.filter(email="sneaky@arogyasystems.in").exists()

    def test_a_vendor_cannot_run_the_grant_action(self, sign_in, vendor_member):
        client = sign_in(vendor_member)

        client.post(
            reverse("admin:users_user_changelist"),
            data={
                "action": "grant_ohc_team",
                "index": "0",
                "_selected_action": [str(vendor_member.pk)],
            },
        )
        vendor_member.refresh_from_db()

        assert vendor_member.is_ohc_team is False

    def test_a_staff_account_without_change_permission_cannot_run_the_action(
        self,
        sign_in,
        staff_but_not_superuser,
    ):
        """Staff alone is not enough to hand out OHC team access."""
        client = sign_in(staff_but_not_superuser)

        response = client.post(
            reverse("admin:users_user_changelist"),
            data={
                "action": "grant_ohc_team",
                "index": "0",
                "_selected_action": [str(staff_but_not_superuser.pk)],
            },
        )
        staff_but_not_superuser.refresh_from_db()

        assert response.status_code == HTTPStatus.FORBIDDEN
        assert staff_but_not_superuser.is_ohc_team is False

    def test_a_staff_account_with_change_permission_cannot_escalate_itself(
        self,
        sign_in,
        staff_but_not_superuser,
    ):
        staff_but_not_superuser.user_permissions.add(
            Permission.objects.get(codename="change_user"),
        )
        client = sign_in(staff_but_not_superuser)

        client.post(
            reverse("admin:users_user_changelist"),
            data={
                "action": "grant_ohc_team",
                "index": "0",
                "_selected_action": [str(staff_but_not_superuser.pk)],
            },
        )
        staff_but_not_superuser.refresh_from_db()

        assert staff_but_not_superuser.is_ohc_team is False
