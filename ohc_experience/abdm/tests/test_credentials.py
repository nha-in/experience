"""Sandbox credentials: issue, reveal, rotate, revoke, URLs and the callback monitor."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm import services
from ohc_experience.abdm.callback import CallbackResult
from ohc_experience.abdm.models import AuditLog
from ohc_experience.abdm.tasks import run_callback_checks
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import MembershipFactory

pytestmark = pytest.mark.django_db

CALLBACK = "https://sandbox.example.in/abdm/callbacks"
BRIDGE = "https://sandbox.example.in/abdm/bridge"
ALERT_STREAK = 3


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def verified_product(product):
    product.organisation.set_verification(Organisation.VerificationStatus.VERIFIED)
    services.issue_credentials(product=product)
    mail.outbox.clear()
    product.refresh_from_db()
    return product


def ok(url):
    return CallbackResult(200, 42)


def down(url):
    return CallbackResult(None, None, "Connection refused")


class TestIssueAndLifecycle:
    def test_nothing_is_issued_before_verification(self, product):
        assert services.issue_credentials(product=product) is None
        assert not hasattr(product, "credential")

    def test_verification_issues_once_and_emails_the_integrator(self, product):
        product.organisation.set_verification(Organisation.VerificationStatus.VERIFIED)

        credential = services.issue_credentials(product=product)
        again = services.issue_credentials(product=product)

        assert credential is not None
        assert again.pk == credential.pk
        assert credential.gateway_base_url.startswith("https://")
        assert len(credential.secret) == services.SECRET_LENGTH
        assert credential.rotation_due > timezone.localdate()
        assert mail.outbox[-1].subject.startswith("[ABDM sandbox] Sandbox credentials")

    def test_rotation_replaces_the_secret_and_keeps_the_client_id(
        self,
        verified_product,
        owner_membership,
    ):
        credential = verified_product.credential
        old_secret = credential.secret
        old_client_id = credential.client_id

        rotated = services.rotate_credentials(
            product=verified_product,
            user=owner_membership.user,
        )

        assert rotated.secret != old_secret
        assert rotated.client_id == old_client_id
        assert rotated.rotated_on is not None
        assert AuditLog.objects.filter(action="rotate").exists()

    def test_revoke_then_request_reissues_with_a_new_client_id(
        self,
        verified_product,
        owner_membership,
    ):
        old_client_id = verified_product.credential.client_id

        services.revoke_credentials(
            product=verified_product,
            user=owner_membership.user,
        )
        verified_product.refresh_from_db()
        assert verified_product.credential.is_active is False
        assert verified_product.active_credential is None

        with pytest.raises(ValidationError, match="no active"):
            services.rotate_credentials(
                product=verified_product,
                user=owner_membership.user,
            )

        reissued = services.request_credentials(
            product=verified_product,
            user=owner_membership.user,
        )

        assert reissued.is_active
        assert reissued.client_id != old_client_id

    def test_a_support_member_cannot_touch_credentials(self, verified_product):
        support = MembershipFactory.create(
            organisation=verified_product.organisation,
            role=Role.SUPPORT,
        )

        with pytest.raises(PermissionDenied):
            services.rotate_credentials(product=verified_product, user=support.user)


class TestReveal:
    def test_reveal_returns_the_secret_and_writes_an_audit_row(
        self,
        verified_product,
        owner_membership,
    ):
        secret = services.reveal_secret(
            product=verified_product,
            user=owner_membership.user,
        )

        assert secret == verified_product.credential.secret
        entry = AuditLog.objects.get(action="reveal_secret")
        assert entry.actor == owner_membership.user
        assert entry.model_label == "abdm.credential"

    def test_reveal_is_rate_limited_per_user(
        self,
        verified_product,
        owner_membership,
        settings,
    ):
        settings.ABDM_SECRET_REVEAL_LIMIT = 2
        for _ in range(2):
            services.reveal_secret(product=verified_product, user=owner_membership.user)

        with pytest.raises(PermissionDenied, match="revealed 2 times"):
            services.reveal_secret(product=verified_product, user=owner_membership.user)

        # Another person has their own allowance.
        admin = MembershipFactory.create(
            organisation=verified_product.organisation,
            role=Role.ADMIN,
        )
        assert services.reveal_secret(product=verified_product, user=admin.user)


class TestCallbackMonitor:
    def test_saving_urls_resets_the_check_history(
        self,
        verified_product,
        owner_membership,
    ):
        credential = services.update_callback_urls(
            product=verified_product,
            user=owner_membership.user,
            callback_url=CALLBACK,
            bridge_url=BRIDGE,
        )
        services.check_callback(product=verified_product, probe=down)

        changed = services.update_callback_urls(
            product=verified_product,
            user=owner_membership.user,
            callback_url="https://sandbox.example.in/abdm/v2/callbacks",
            bridge_url=BRIDGE,
        )

        assert credential.callback_url == CALLBACK
        assert changed.callback_failure_streak == 0
        assert changed.callback_checked_at is None

    def test_a_reachable_callback_is_recorded(self, verified_product, owner_membership):
        services.update_callback_urls(
            product=verified_product,
            user=owner_membership.user,
            callback_url=CALLBACK,
            bridge_url="",
        )

        credential = services.check_callback(product=verified_product, probe=ok)

        assert credential.callback_ok is True
        assert credential.callback_status_code == HTTPStatus.OK
        assert credential.callback_latency_ms == 42  # noqa: PLR2004
        assert credential.callback_variant == "success"
        assert credential.callback_label == "Reachable"

    def test_the_third_failure_in_a_row_emails_the_integrator(
        self,
        verified_product,
        owner_membership,
    ):
        services.update_callback_urls(
            product=verified_product,
            user=owner_membership.user,
            callback_url=CALLBACK,
            bridge_url="",
        )

        for _ in range(ALERT_STREAK):
            credential = services.check_callback(product=verified_product, probe=down)

        assert credential.callback_failure_streak == ALERT_STREAK
        assert len(mail.outbox) == 1
        assert "Callback URL unreachable" in mail.outbox[0].subject
        assert owner_membership.user.email in mail.outbox[0].to

        # A fourth failure does not nag again; a success resets the streak.
        services.check_callback(product=verified_product, probe=down)
        assert len(mail.outbox) == 1
        assert (
            services.check_callback(
                product=verified_product,
                probe=ok,
            ).callback_failure_streak
            == 0
        )

    def test_checking_without_a_url_is_refused(self, verified_product):
        with pytest.raises(ValidationError, match="Set a callback URL"):
            services.check_callback(product=verified_product, probe=ok)

    def test_the_scheduled_task_probes_every_active_credential(
        self,
        verified_product,
        owner_membership,
        monkeypatch,
    ):
        services.update_callback_urls(
            product=verified_product,
            user=owner_membership.user,
            callback_url=CALLBACK,
            bridge_url="",
        )
        monkeypatch.setattr(services, "probe_callback", ok)

        assert run_callback_checks() == 1
        verified_product.credential.refresh_from_db()
        assert verified_product.credential.callback_ok is True


class TestCredentialViews:
    def url(self, product, name="credentials"):
        return reverse(f"products:{name}", args=[product.sandbox_id])

    def test_the_page_masks_the_secret(
        self,
        sign_in,
        owner_membership,
        verified_product,
    ):
        response = sign_in(owner_membership.user).get(self.url(verified_product))
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert response.context["nav_section"] == "credentials"
        assert verified_product.credential.client_id in html
        assert verified_product.credential.secret not in html
        assert "Reveal" in html
        assert "Synthetic data only." in html

    def test_an_unverified_organisation_sees_why_there_is_nothing(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        html = sign_in(owner_membership.user).get(self.url(product)).content.decode()

        assert "issued once the NHA review team verifies" in html

    def test_an_htmx_reveal_returns_the_secret_fragment(
        self,
        sign_in,
        owner_membership,
        verified_product,
    ):
        response = sign_in(owner_membership.user).post(
            self.url(verified_product, "credentials-reveal"),
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert 'id="secret-reveal"' in html
        assert verified_product.credential.secret in html
        assert "<!DOCTYPE html>" not in html

    def test_a_plain_reveal_shows_the_secret_on_the_page_once(
        self,
        sign_in,
        owner_membership,
        verified_product,
    ):
        client = sign_in(owner_membership.user)

        response = client.post(self.url(verified_product, "credentials-reveal"))
        assert response.status_code == HTTPStatus.OK
        assert verified_product.credential.secret in response.content.decode()

        # A plain GET afterwards is masked again.
        assert (
            verified_product.credential.secret
            not in client.get(
                self.url(verified_product),
            ).content.decode()
        )

    def test_a_support_member_cannot_reveal(self, sign_in, verified_product):
        support = MembershipFactory.create(
            organisation=verified_product.organisation,
            role=Role.SUPPORT,
        )

        response = sign_in(support.user).post(
            self.url(verified_product, "credentials-reveal"),
        )

        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_rotate_and_revoke_redirect_back_without_scripting(
        self,
        sign_in,
        owner_membership,
        verified_product,
    ):
        client = sign_in(owner_membership.user)
        old_secret = verified_product.credential.secret

        rotated = client.post(self.url(verified_product, "credentials-rotate"))
        assert rotated.status_code == HTTPStatus.FOUND
        assert rotated["Location"] == self.url(verified_product)
        verified_product.credential.refresh_from_db()
        assert verified_product.credential.secret != old_secret

        revoked = client.post(self.url(verified_product, "credentials-revoke"))
        assert revoked.status_code == HTTPStatus.FOUND
        verified_product.credential.refresh_from_db()
        assert verified_product.credential.is_active is False
        assert (
            "Request new credentials"
            in client.get(self.url(verified_product)).content.decode()
        )

    def test_an_htmx_revoke_swaps_the_card(
        self,
        sign_in,
        owner_membership,
        verified_product,
    ):
        response = sign_in(owner_membership.user).post(
            self.url(verified_product, "credentials-revoke"),
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert 'id="credentials-card"' in html
        assert "Revoked" in html
        assert 'hx-swap-oob="innerHTML"' in html

    def test_saving_urls_requires_https(
        self,
        sign_in,
        owner_membership,
        verified_product,
    ):
        response = sign_in(owner_membership.user).post(
            self.url(verified_product, "credentials-urls"),
            data={"callback_url": "http://insecure.example.in/cb", "bridge_url": ""},
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert "Use an HTTPS URL." in html
        verified_product.credential.refresh_from_db()
        assert verified_product.credential.callback_url == ""

    def test_saving_urls_and_testing_the_callback(
        self,
        sign_in,
        owner_membership,
        verified_product,
        monkeypatch,
    ):
        monkeypatch.setattr(services, "probe_callback", ok)
        client = sign_in(owner_membership.user)

        saved = client.post(
            self.url(verified_product, "credentials-urls"),
            data={"callback_url": CALLBACK, "bridge_url": BRIDGE},
        )
        assert saved.status_code == HTTPStatus.FOUND

        tested = client.post(
            self.url(verified_product, "credentials-test"),
            headers={"HX-Request": "true"},
        )
        html = tested.content.decode()

        assert tested.status_code == HTTPStatus.OK
        assert 'id="callback-card"' in html
        assert "Reachable" in html
        assert "HTTP 200" in html
        assert "42 ms" in html
