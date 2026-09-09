"""Products: registration, editing, milestone locks, the overview and the sidebar."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm import services
from ohc_experience.abdm.forms import ProductForm
from ohc_experience.abdm.models import AuditLog
from ohc_experience.abdm.models import ComplianceRecord
from ohc_experience.abdm.models import Product
from ohc_experience.abdm.models import ReviewHistory
from ohc_experience.abdm.models import ReviewItem
from ohc_experience.abdm.tests.factories import ProductFactory
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PRODUCT_DATA = {
    "name": "Arogya HMIS",
    "description": "Hospital management with ABHA-linked registration.",
    "category": Product.Category.HMIS,
    "solution_type": Product.SolutionType.CLINICAL_HMIS,
    "milestones": ["HI-CM:M1", "HI-CM:M2", "PHR:PHR1"],
}


@pytest.fixture
def reviewer(db):
    return UserFactory.create(email="reviewer@nha.gov.in", is_ohc_team=True)


def statuses(product) -> dict[str, str]:
    return {record.key: record.status for record in product.compliance_records.all()}


class TestRegisterProduct:
    def test_registration_creates_records_an_item_and_a_sandbox_id(
        self,
        owner_membership,
        reviewer,
    ):
        product = services.register_product(
            organisation=owner_membership.organisation,
            user=owner_membership.user,
            data=PRODUCT_DATA,
        )

        year = timezone.localdate().year
        assert product.sandbox_id == f"SBX-{year}-00001"
        assert product.registration_status == Product.RegistrationStatus.PENDING
        assert product.applied_tracks == ["HI-CM", "PHR"]
        assert product.applied_milestones == ["HI-CM:M1", "HI-CM:M2", "PHR:PHR1"]
        assert statuses(product) == {
            "HI-CM:M1": ComplianceRecord.Status.OPEN,
            "HI-CM:M2": ComplianceRecord.Status.LOCKED,
            "PHR:PHR1": ComplianceRecord.Status.LOCKED,
        }
        item = ReviewItem.objects.get(item_type=ReviewItem.Type.PRODUCT_REGISTRATION)
        assert item.product == product
        assert item.status == ReviewItem.Status.NEW
        assert AuditLog.objects.filter(model_label="abdm.product", action="create")
        assert mail.outbox[0].to == [reviewer.email]
        assert "Product registration" in mail.outbox[0].subject

    def test_phr_m1_alone_creates_the_shared_hi_cm_record(self, owner_membership):
        product = services.register_product(
            organisation=owner_membership.organisation,
            user=owner_membership.user,
            data={**PRODUCT_DATA, "milestones": ["PHR:M1", "PHR:PHR1"]},
        )

        assert product.applied_tracks == ["HI-CM", "PHR"]
        assert statuses(product) == {
            "HI-CM:M1": ComplianceRecord.Status.OPEN,
            "PHR:PHR1": ComplianceRecord.Status.LOCKED,
        }

    def test_a_later_milestone_needs_the_earlier_one(self, owner_membership):
        with pytest.raises(ValidationError, match="M2 on HI-CM needs M1"):
            services.register_product(
                organisation=owner_membership.organisation,
                user=owner_membership.user,
                data={**PRODUCT_DATA, "milestones": ["HI-CM:M2"]},
            )

        assert not Product.objects.exists()

    def test_a_support_member_cannot_register(self, onboarded_organisation):
        support = MembershipFactory.create(
            organisation=onboarded_organisation,
            role=Role.SUPPORT,
        )

        with pytest.raises(PermissionDenied):
            services.register_product(
                organisation=onboarded_organisation,
                user=support.user,
                data=PRODUCT_DATA,
            )

    def test_a_verified_organisation_gets_credentials_at_once(self, owner_membership):
        organisation = owner_membership.organisation
        organisation.set_verification(Organisation.VerificationStatus.VERIFIED)

        product = services.register_product(
            organisation=organisation,
            user=owner_membership.user,
            data=PRODUCT_DATA,
        )

        assert product.credential.is_active
        assert product.credential.client_id.startswith("SBX_")
        assert any("credentials" in message.subject.lower() for message in mail.outbox)

    def test_an_unverified_organisation_gets_no_credentials(self, owner_membership):
        product = services.register_product(
            organisation=owner_membership.organisation,
            user=owner_membership.user,
            data=PRODUCT_DATA,
        )

        assert not hasattr(product, "credential")


class TestUpdateProduct:
    @pytest.fixture
    def product(self, owner_membership) -> Product:
        return services.register_product(
            organisation=owner_membership.organisation,
            user=owner_membership.user,
            data=PRODUCT_DATA,
        )

    def test_adding_a_milestone_creates_a_record_and_tells_the_reviewers(
        self,
        product,
        owner_membership,
        reviewer,
    ):
        mail.outbox.clear()

        updated = services.update_product(
            product=product,
            user=owner_membership.user,
            data={
                **PRODUCT_DATA,
                "milestones": [*PRODUCT_DATA["milestones"], "UHI:UHI1"],
            },
        )

        assert statuses(updated)["UHI:UHI1"] == ComplianceRecord.Status.OPEN
        assert updated.applied_tracks == ["HI-CM", "UHI", "PHR"]
        item = updated.review_items.get(item_type=ReviewItem.Type.PRODUCT_REGISTRATION)
        entry = item.history.get(kind=ReviewHistory.Kind.MILESTONES_CHANGED)
        assert entry.payload == {"added": ["UHI:UHI1"], "removed": []}
        assert mail.outbox[-1].to == [reviewer.email]
        assert "UHI:UHI1" in mail.outbox[-1].body

    def test_removing_an_open_milestone_deletes_its_record(
        self,
        product,
        owner_membership,
    ):
        updated = services.update_product(
            product=product,
            user=owner_membership.user,
            data={**PRODUCT_DATA, "milestones": ["HI-CM:M1", "HI-CM:M2"]},
        )

        assert "PHR:PHR1" not in statuses(updated)
        assert updated.applied_tracks == ["HI-CM"]

    def test_an_approved_milestone_cannot_be_removed(self, product, owner_membership):
        record = product.compliance_records.get(milestone_code="M1")
        record.status = ComplianceRecord.Status.APPROVED
        record.save()

        with pytest.raises(ValidationError, match="cannot be removed"):
            services.update_product(
                product=product,
                user=owner_membership.user,
                data={**PRODUCT_DATA, "milestones": ["UHI:UHI1"]},
            )

    def test_the_form_refuses_to_drop_a_locked_milestone(self, product):
        record = product.compliance_records.get(milestone_code="M1")
        record.status = ComplianceRecord.Status.UNDER_REVIEW
        record.save()

        form = ProductForm(
            {**PRODUCT_DATA, "milestones": ["UHI:UHI1"]},
            instance=product,
        )

        assert not form.is_valid()
        assert "cannot be removed: it is under review" in form.errors["milestones"][0]
        assert "HI-CM:M1" in form.locked_keys

    def test_editing_a_sent_back_product_resubmits_it(
        self,
        product,
        owner_membership,
        reviewer,
    ):
        item = product.review_items.get(item_type=ReviewItem.Type.PRODUCT_REGISTRATION)
        item.status = ReviewItem.Status.SENT_BACK
        item.save()
        product.registration_status = Product.RegistrationStatus.SENT_BACK
        product.sent_back_reason = "Name the actual product, not the company."
        product.save()

        updated = services.update_product(
            product=product,
            user=owner_membership.user,
            data={**PRODUCT_DATA, "name": "Arogya HMIS v4"},
        )

        item.refresh_from_db()
        assert updated.registration_status == Product.RegistrationStatus.PENDING
        assert updated.sent_back_reason == ""
        assert item.status == ReviewItem.Status.IN_REVIEW
        assert item.resubmission_count == 1


class TestMilestoneLocks:
    def test_approving_m1_opens_m2_and_phr1(self, owner_membership):
        product = services.register_product(
            organisation=owner_membership.organisation,
            user=owner_membership.user,
            data=PRODUCT_DATA,
        )
        record = product.compliance_records.get(milestone_code="M1")
        record.status = ComplianceRecord.Status.APPROVED
        record.save()

        services.sync_milestone_locks(product)

        assert statuses(product) == {
            "HI-CM:M1": ComplianceRecord.Status.APPROVED,
            "HI-CM:M2": ComplianceRecord.Status.OPEN,
            "PHR:PHR1": ComplianceRecord.Status.OPEN,
        }

    def test_a_milestone_stays_locked_while_the_previous_is_in_progress(
        self,
        owner_membership,
    ):
        product = services.register_product(
            organisation=owner_membership.organisation,
            user=owner_membership.user,
            data={**PRODUCT_DATA, "milestones": ["HI-CM:M1", "HI-CM:M2", "HI-CM:M3"]},
        )
        record = product.compliance_records.get(milestone_code="M1")
        record.status = ComplianceRecord.Status.UNDER_REVIEW
        record.save()

        services.sync_milestone_locks(product)

        assert statuses(product)["HI-CM:M2"] == ComplianceRecord.Status.LOCKED
        assert statuses(product)["HI-CM:M3"] == ComplianceRecord.Status.LOCKED


class TestProductViews:
    @pytest.fixture
    def product(self, owner_membership) -> Product:
        return services.register_product(
            organisation=owner_membership.organisation,
            user=owner_membership.user,
            data=PRODUCT_DATA,
        )

    def test_onboarding_step_three_renders_the_form(self, sign_in, owner_membership):
        response = sign_in(owner_membership.user).get(
            reverse("products:onboarding-product"),
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert response.context["onboarding_step"] == 3  # noqa: PLR2004
        assert "HFR registration" in html
        assert "No milestones published yet." in html
        assert "Same record as HI-CM M1" in html

    def test_onboarding_step_three_needs_the_organisation_step_first(
        self,
        sign_in,
        organisation,
    ):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.OWNER,
        )

        response = sign_in(membership.user).get(reverse("products:onboarding-product"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("organisations:onboarding")

    def test_registering_from_onboarding_lands_on_the_overview(
        self,
        sign_in,
        owner_membership,
    ):
        response = sign_in(owner_membership.user).post(
            reverse("products:onboarding-product"),
            data=PRODUCT_DATA,
        )

        product = Product.objects.get()
        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == product.get_absolute_url()

    def test_an_htmx_registration_hands_the_client_the_redirect(
        self,
        sign_in,
        owner_membership,
    ):
        response = sign_in(owner_membership.user).post(
            reverse("products:onboarding-product"),
            data=PRODUCT_DATA,
            headers={"HX-Request": "true"},
        )

        assert response.status_code == HTTPStatus.OK
        assert response["HX-Redirect"] == Product.objects.get().get_absolute_url()

    def test_an_invalid_htmx_registration_swaps_the_form_back(
        self,
        sign_in,
        owner_membership,
    ):
        response = sign_in(owner_membership.user).post(
            reverse("products:onboarding-product"),
            data={**PRODUCT_DATA, "milestones": ["HI-CM:M2"]},
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert '<form id="product-form"' in html
        assert "<!DOCTYPE html>" not in html
        assert "M2 on HI-CM needs M1" in html
        assert not Product.objects.exists()

    def test_the_dashboard_hop_lands_on_the_product(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        response = sign_in(owner_membership.user).get(reverse("dashboard"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == product.get_absolute_url()

    def test_the_dashboard_hop_sends_a_productless_organisation_to_step_three(
        self,
        sign_in,
        owner_membership,
    ):
        response = sign_in(owner_membership.user).get(reverse("dashboard"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("products:onboarding-product")

    def test_the_overview_shows_tracks_activity_and_the_sidebar(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        response = sign_in(owner_membership.user).get(product.get_absolute_url())
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert response.context["nav_section"] == "overview"
        assert "1 of 1 milestones approved" not in html
        assert "0 of 2 milestones approved" in html
        assert "M1 · Open" in html
        assert "M2 · Locked" in html
        assert "Submitted for review" in html
        assert "Issued once the organisation is verified." in html
        nav = html[html.index('<nav id="app-nav"') : html.index("</nav>")]
        for label in (
            "Overview",
            "Credentials",
            "HI-CM",
            "UHI",
            "NHCX",
            "PHR",
            "HealthLocker",
            "Events",
            "Support",
            "Edit product",
            "Settings",
        ):
            assert label in nav

    def test_another_organisations_product_is_a_404(self, sign_in, owner_membership):
        other = ProductFactory.create(
            organisation=OrganisationFactory.create(onboarded=True),
        )

        response = sign_in(owner_membership.user).get(other.get_absolute_url())

        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_edit_keeps_locked_milestones_and_saves_changes(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        record = product.compliance_records.get(milestone_code="M1")
        record.status = ComplianceRecord.Status.APPROVED
        record.save()
        edit_url = reverse("products:edit", args=[product.sandbox_id])

        page = sign_in(owner_membership.user).get(edit_url).content.decode()
        assert 'name="milestones" value="HI-CM:M1"' in page
        assert "disabled" in page

        response = sign_in(owner_membership.user).post(
            edit_url,
            data={**PRODUCT_DATA, "name": "Arogya HMIS v4"},
        )

        assert response.status_code == HTTPStatus.FOUND
        product.refresh_from_db()
        assert product.name == "Arogya HMIS v4"

    def test_register_another_product_from_the_shell(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        response = sign_in(owner_membership.user).post(
            reverse("products:new"),
            data={**PRODUCT_DATA, "name": "Arogya LMIS", "milestones": ["UHI:UHI1"]},
        )

        assert response.status_code == HTTPStatus.FOUND
        assert Product.objects.count() == 2  # noqa: PLR2004
        second = Product.objects.get(name="Arogya LMIS")
        assert response["Location"] == second.get_absolute_url()

    def test_a_support_member_sees_the_form_read_only(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        support = MembershipFactory.create(
            organisation=owner_membership.organisation,
            role=Role.SUPPORT,
        )

        response = sign_in(support.user).get(
            reverse("products:edit", args=[product.sandbox_id]),
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert "Support members can read product details" in html
        assert "Save product" not in html

        refused = sign_in(support.user).post(
            reverse("products:edit", args=[product.sandbox_id]),
            data=PRODUCT_DATA,
        )
        assert refused.status_code == HTTPStatus.FORBIDDEN
