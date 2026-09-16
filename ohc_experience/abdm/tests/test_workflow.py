# ruff: noqa: PLR2004
import re
import socket
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.datastructures import MultiValueDict

from ohc_experience.abdm.catalog import MILESTONES
from ohc_experience.abdm.catalog import TRACK_MAP
from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.demo import organisation_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.demo import uhi_data
from ohc_experience.abdm.forms import ExitEvidenceForm
from ohc_experience.abdm.forms import OrganisationForm
from ohc_experience.abdm.forms import ProductRegistrationForm
from ohc_experience.experiences import credentials
from ohc_experience.experiences import uploads
from ohc_experience.experiences import workflows as services
from ohc_experience.experiences.definitions import TrackDefinition
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.experiences.registry import get_program
from ohc_experience.integrations.local import fail_next
from ohc_experience.integrations.models import ProvisionedResource
from ohc_experience.integrations.models import ProvisionedResourceState
from ohc_experience.integrations.models import ProvisioningRun
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.selectors import awaiting_provisioning
from ohc_experience.integrations.selectors import provisioning_can_be_retried
from ohc_experience.integrations.services import provision_inline
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.users.tests.factories import ReviewerFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def pdf(name="test.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4\n%%EOF", content_type="application/pdf")


def stored_secret(product):
    credential = ProductCredential.objects.get(product=product)
    return credentials.cipher().decrypt(credential.encrypted_secret.encode()).decode()


def files():
    return MultiValueDict(
        {
            "wasa_certificate": [pdf("wasa.pdf")],
            "functional_certificate": [pdf("certificate.pdf")],
            "functional_report": [pdf("report.pdf")],
        },
    )


@pytest.fixture
def environment(settings, tmp_path, lgd_lookup):
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
    settings.MEDIA_ROOT = tmp_path
    cache.clear()
    applicant = UserFactory()
    admin = UserFactory(is_superuser=True, is_staff=True, is_nha_team=True)
    reviewer = ReviewerFactory(is_nha_team=True, is_staff=True)
    outsider = UserFactory()
    org = Organisation.objects.create(name="Test health systems")
    Membership.objects.create(organisation=org, user=applicant, role="owner")
    item = services.organisation_review(org, applicant)
    item, form, saved = services.save_review_form(
        item,
        applicant,
        data=organisation_data(),
        files={"supporting_document": pdf()},
        submit=True,
    )
    assert saved, form.errors
    services.assign_review(item, admin, reviewer)
    services.decide(item, reviewer, action="approve", note="Identity verified.")
    org.refresh_from_db()
    workspace, form = services.register_product(org, applicant, data=product_data())
    assert workspace, form.errors
    provision_inline(workspace.product)
    workspace.refresh_from_db()
    return {
        "applicant": applicant,
        "admin": admin,
        "reviewer": reviewer,
        "outsider": outsider,
        "org": org,
        "workspace": workspace,
    }


def milestone(environment, key="m1"):
    return (
        environment["workspace"].product.milestones.get(key=key).application.review_item
    )


def submit(environment, key="m1"):
    """UHI participation answers a different form and needs no attachments."""
    uhi = key == "uhi1"
    item, form, saved = services.save_review_form(
        milestone(environment, key),
        environment["applicant"],
        data=uhi_data() if uhi else evidence_data(),
        files=None if uhi else files(),
        submit=True,
    )
    assert saved, form.errors
    return item


def approve(environment, key="m1"):
    submit(environment, key)
    return approve_submitted(environment, key)


def approve_submitted(environment, key="m1"):
    item = milestone(environment, key)
    services.assign_review(item, environment["admin"], environment["reviewer"])
    return services.decide(
        item,
        environment["reviewer"],
        action="approve",
        note="Approved with gateway handoff.",
    )


def waiting_on(environment, key):
    item = milestone(environment, key)
    return [prerequisite.name for prerequisite in services.pending_prerequisites(item)]


def reverify(environment):
    """Organisation details resubmitted, so verification is pending again."""
    item = environment["org"].review_items.get(kind="organisation_verification")
    item, form, saved = services.save_review_form(
        item,
        environment["applicant"],
        data=organisation_data(),
        files={"supporting_document": pdf()},
        submit=True,
    )
    assert saved, form.errors
    environment["org"].refresh_from_db()
    assert not environment["org"].is_verified
    return item


def test_shared_m1_and_independent_tracks(environment):
    product = environment["workspace"].product
    assert product.milestones.filter(key="m1").count() == 1
    assert TRACK_MAP["PHR"].keys == ("phr1",)
    assert TRACK_MAP["HealthLocker"].keys == ("locker1",)
    assert TRACK_MAP["NHCX"].keys == ("nhcx1",)
    assert get_program().track_milestones(TRACK_MAP["PHR"]) == ("m1", "phr1")
    assert MILESTONES["uhi1"].predecessor == MILESTONES["nhcx1"].predecessor == "m1"
    for key in ("m2", "m3", "phr1", "uhi1"):
        assert waiting_on(environment, key) == ["M1 - ABHA and identity"]
    for key in ("m4", "locker1"):
        assert waiting_on(environment, key) == []
    approve(environment)
    for key in ("m2", "m3", "phr1", "uhi1"):
        assert waiting_on(environment, key) == []
    assert product.outcomes.filter(outcome_type="milestone_approval").exists()


def test_a_review_waits_on_every_earlier_milestone_and_the_organisation(environment):
    """Earliest first, so a reviewer can see where the chain is held up."""
    reverify(environment)

    assert waiting_on(environment, "m3") == [
        "organisation verification",
        "M1 - ABHA and identity",
    ]
    for key in ("m4", "locker1"):
        assert waiting_on(environment, key) == ["organisation verification"]


def test_uhi_shows_m1_as_a_prerequisite_it_does_not_offer(environment):
    """M1 is one shared record, so approving it completes it on UHI too."""
    assert TRACK_MAP["UHI"].keys == ("uhi1",)
    assert get_program().track_milestones(TRACK_MAP["UHI"]) == ("m1", "uhi1")
    product = environment["workspace"].product
    assert product.milestones.filter(key="m1").count() == 1

    approve(environment)

    assert product.milestones.get(key="m1").application.status == "approved"


def test_a_shared_milestone_names_the_other_tracks_not_an_owner(environment):
    """M1 is offered by HIE-CM; every track that depends on it names the rest."""
    program = get_program()

    assert set(program.shared_with("m1", "PHR")) == {"HIE-CM", "UHI", "NHCX"}
    assert set(program.shared_with("m1", "HIE-CM")) == {"PHR", "UHI", "NHCX"}
    assert program.shared_with("locker1", "HealthLocker") == ()
    assert MILESTONES["locker1"].code == "HL1"


def test_a_tracks_description_names_its_shared_milestones(environment, client):
    """The sentence was hand-written on three tracks and stale on all three."""
    program = get_program()
    assert program.shared_note("PHR") == "M1 is shared with HIE-CM, UHI and NHCX."
    assert program.shared_note("HIE-CM") == "M1 is shared with UHI, NHCX and PHR."
    assert program.shared_note("HealthLocker") == ""
    client.force_login(environment["applicant"])

    html = client.get(
        reverse("experiences:track", args=[environment["workspace"].reference, "PHR"]),
    ).content.decode()

    assert "M1 is shared with HIE-CM, UHI and NHCX." in html


def test_a_predecessor_no_track_offers_can_never_unlock():
    class Stranded(get_program()):
        key = "stranded"
        tracks = (TrackDefinition("UHI", "UHI", "", ("uhi1",)),)

    with pytest.raises(ImproperlyConfigured, match="can never unlock"):
        Stranded.validate()


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("PHR", "Shared with HIE-CM and UHI"),
        ("UHI", "Shared with HIE-CM and PHR"),
        ("HIE-CM", "Shared with UHI and PHR"),
    ],
)
def test_the_shared_m1_note_follows_the_catalogue_not_a_hardcoded_track(
    environment,
    client,
    code,
    expected,
):
    """The tile names only the tracks this product applied for. NHCX shares M1 in
    the catalogue, so the track description names it, but no tile does."""
    client.force_login(environment["applicant"])

    html = client.get(
        reverse("experiences:track", args=[environment["workspace"].reference, code]),
    ).content.decode()

    assert f'<span class="ui-station-alias">{expected}</span>' in html


def test_uhi_participation_is_recorded_rather_than_decided(environment):
    """Legacy never reviewed UHI, so submitting is the whole process."""
    approve(environment)

    item = submit(environment, "uhi1")

    assert item.status == ReviewItem.Status.APPROVED
    assert item.decided_at is not None
    assert item.decided_by is None
    assert item.application.status == "approved"
    assert item.selected_submission.data["uhi_role"] == ["eua"]


def test_a_recorded_uhi_application_still_reaches_the_queue(environment, client):
    approve(environment)
    item = submit(environment, "uhi1")
    client.force_login(environment["admin"])

    page = client.get(
        reverse("experiences:queue"),
        {"scope": "decided"},
        HTTP_HX_REQUEST="true",
    )

    assert item in list(page.context["page"])


def test_nobody_decides_a_uhi_application_twice(environment):
    approve(environment)
    item = submit(environment, "uhi1")
    services.assign_review(item, environment["admin"], environment["reviewer"])

    with pytest.raises(ValidationError, match="not awaiting a decision"):
        services.decide(item, environment["reviewer"], action="approve")


def test_uhi_answers_can_be_corrected_after_recording(environment):
    approve(environment)
    item = submit(environment, "uhi1")

    item, form, saved = services.save_review_form(
        item,
        environment["applicant"],
        data={**uhi_data(), "uhi_role": ["hspa"]},
        submit=True,
    )

    assert saved, form.errors
    assert item.selected_submission.data["uhi_role"] == ["hspa"]
    assert item.status == ReviewItem.Status.APPROVED
    assert item.history.filter(action="Record updated").exists()


def test_uhi_submitted_before_m1_is_approved_is_recorded_when_it_is(environment):
    submit(environment)
    uhi = submit(environment, "uhi1")
    assert uhi.status == ReviewItem.Status.NEW
    assert uhi.application.status == "under_review"
    services.assign_review(uhi, environment["admin"], environment["reviewer"])
    for action in ("approve", "send_back", "query"):
        with pytest.raises(ValidationError, match="recorded once its prerequisites"):
            services.decide(uhi, environment["reviewer"], action=action, note="Hold.")

    approve_submitted(environment)

    uhi.refresh_from_db()
    assert uhi.status == ReviewItem.Status.APPROVED
    assert uhi.decided_by is None
    assert ReviewItem.objects.get(pk=uhi.pk).application.status == "approved"
    assert uhi.history.filter(action="Recorded", actor=None).exists()


def test_a_waiting_uhi_application_is_recorded_once_verification_is_approved(
    environment,
):
    """Verification is no milestone's dependency, but approving it still counts."""
    approve(environment)
    verification = reverify(environment)
    uhi = submit(environment, "uhi1")
    assert uhi.status == ReviewItem.Status.NEW
    services.assign_review(verification, environment["admin"], environment["reviewer"])

    services.decide(verification, environment["reviewer"], action="approve")

    uhi.refresh_from_db()
    assert uhi.status == ReviewItem.Status.APPROVED


def test_milestones_are_submitted_in_order_and_decided_in_order(environment):
    """Submitting M1 opens M2, but M2 is decided only once M1 is approved."""
    with pytest.raises(
        ValidationError,
        match="once M1 - ABHA and identity is submitted",
    ):
        submit(environment, "m2")
    submit(environment)
    m2 = submit(environment, "m2")
    assert m2.status == ReviewItem.Status.NEW
    services.assign_review(m2, environment["admin"], environment["reviewer"])
    for action in ("approve", "send_back"):
        with pytest.raises(
            ValidationError,
            match="once M1 - ABHA and identity is approved",
        ):
            services.decide(m2, environment["reviewer"], action=action, note="Not yet.")
    services.decide(
        m2,
        environment["reviewer"],
        action="query",
        note="Which cases cover consent expiry?",
        field_key="functional_report",
    )

    approve_submitted(environment)
    query = m2.queries.get()
    services.reply_query(query, environment["applicant"], "Cases 3 and 4.")
    services.resolve_query(query, environment["reviewer"])
    services.decide(m2, environment["reviewer"], action="approve")

    m2.refresh_from_db()
    assert m2.status == ReviewItem.Status.APPROVED


def test_an_unverified_organisation_can_submit_but_not_be_approved(environment):
    reverify(environment)
    item = submit(environment)
    services.assign_review(item, environment["admin"], environment["reviewer"])

    with pytest.raises(ValidationError) as error:
        services.decide(item, environment["reviewer"], action="approve")

    assert error.value.messages == [
        (
            "Approve or send back this request once organisation verification "
            "is approved."
        ),
    ]


def test_reviewers_with_grants_decide_whoever_is_assigned(environment):
    item = submit(environment)
    for actor in [environment["applicant"], environment["outsider"]]:
        with pytest.raises(PermissionDenied):
            services.decide(item, actor, action="approve")
    with pytest.raises(PermissionDenied):
        services.assign_review(item, environment["reviewer"], environment["reviewer"])
    with pytest.raises(ValidationError):
        services.assign_review(item, environment["admin"], environment["applicant"])
    # Assigned to someone else, the reviewer's grant still lets them decide.
    services.assign_review(item, environment["admin"], environment["admin"])
    services.decide(item, environment["reviewer"], action="approve")
    item.refresh_from_db()
    assert item.assignee == environment["admin"]
    with pytest.raises(ValidationError):
        services.decide(
            item,
            environment["reviewer"],
            action="send_back",
            note="Late change",
        )


def test_queries_pause_until_all_answered_and_resolved(environment):
    item = submit(environment)
    services.assign_review(item, environment["admin"], environment["reviewer"])
    for field in ["wasa_agency", "functional_report"]:
        services.decide(
            item,
            environment["reviewer"],
            action="query",
            note="Please clarify this field.",
            field_key=field,
        )
    first, second = item.queries.all()
    services.reply_query(first, environment["applicant"], "Confirmed the scope.")
    item.refresh_from_db()
    assert item.status == "query_raised"
    with pytest.raises(ValidationError, match="Withdraw"):
        services.save_review_form(item, environment["applicant"], data=evidence_data())
    services.reply_query(second, environment["applicant"], "See case 14.")
    item.refresh_from_db()
    assert item.status == "in_review"
    with pytest.raises(ValidationError, match="Resolve"):
        services.decide(item, environment["reviewer"], action="approve")
    services.resolve_query(first, environment["reviewer"])
    services.resolve_query(second, environment["reviewer"])
    services.decide(item, environment["reviewer"], action="approve")
    assert item.history.filter(action="Query answered").count() == 2
    answered = [m for m in mail.outbox if "Query answered" in m.body]
    assert len(answered) == 2


def test_a_product_edit_applies_at_once_without_a_review(environment):
    workspace = environment["workspace"]
    registered_at = workspace.registered_at
    registration = workspace.product.review_items.get(kind="product_registration")
    assert registration.status == ReviewItem.Status.APPROVED

    item, form, saved = services.save_review_form(
        registration,
        environment["applicant"],
        data={
            **product_data(),
            "applied_milestones": [*workspace.applied_milestones, "NHCX:nhcx1"],
        },
        submit=True,
    )

    assert saved, form.errors
    assert item.status == ReviewItem.Status.APPROVED
    assert item.decided_by is None
    assert item.history.filter(action="Record updated").exists()
    workspace.refresh_from_db()
    assert "NHCX:nhcx1" in workspace.applied_milestones
    assert workspace.product.milestones.get(key="nhcx1").enabled
    assert workspace.registered_at == registered_at
    with pytest.raises(ValidationError, match="Only an active review request"):
        services.withdraw(item, environment["applicant"])


def test_a_milestone_under_review_is_named_when_an_edit_removes_it(environment):
    workspace = environment["workspace"]
    submit(environment, "locker1")
    registration = workspace.product.review_items.get(kind="product_registration")

    with pytest.raises(ValidationError, match="HL1 is under review"):
        services.save_review_form(
            registration,
            environment["applicant"],
            data={**product_data(), "applied_milestones": ["HIE-CM:m1"]},
            submit=True,
        )


def test_the_product_form_saves_changes_rather_than_requesting_review(
    environment,
    client,
):
    workspace = environment["workspace"]
    client.force_login(environment["applicant"])

    html = client.get(
        reverse("experiences:product-edit", args=[workspace.reference]),
    ).content.decode()

    assert re.search(r">\s*Save changes\s*<", html)
    assert "for review" not in html
    assert "Withdraw request" not in html


def test_a_later_milestone_opens_for_evidence_before_the_earlier_is_approved(
    environment,
    client,
):
    submit(environment)
    submit(environment, "m2")
    client.force_login(environment["applicant"])
    url = reverse(
        "experiences:track",
        args=[environment["workspace"].reference, "HIE-CM"],
    )

    m3 = client.get(url, {"milestone": "m3"}).content.decode()
    m2 = client.get(url, {"milestone": "m2"}).content.decode()

    assert "data-request-submit" in m3
    assert "Milestone locked" not in m3
    assert "It can be approved once M1 - ABHA and identity is approved." in m2


def test_registering_a_product_records_it_without_a_review(environment):
    workspace, form = services.register_product(
        environment["org"],
        environment["applicant"],
        data=product_data("Second product"),
    )
    assert workspace, form.errors
    item = workspace.product.review_items.get(kind="product_registration")

    assert item.status == ReviewItem.Status.APPROVED
    assert item.application.status == "approved"
    assert workspace.registered_at == item.decided_at
    assert item.history.filter(action="Recorded").exists()
    assert not item.history.filter(action="Requested review").exists()
    notice = mail.outbox[-1]
    assert "Product Registration" in notice.subject
    assert "has registered a new product, Second product" in notice.body


def test_withdraw_and_resubmit_preserves_original_fields_and_files(environment):
    item = submit(environment)
    original = item.selected_submission
    original_data = dict(original.data)
    services.withdraw(item, environment["applicant"])
    item, form, saved = services.save_review_form(
        item,
        environment["applicant"],
        data={**evidence_data(), "wasa_agency": "M/s A3S Tech & Company"},
        submit=True,
    )
    assert saved, form.errors
    original.refresh_from_db()
    assert original.data == original_data
    assert item.selected_submission.pk != original.pk
    assert item.selected_submission.revision == 2
    assert item.selected_submission.attachments.count() == original.attachments.count()
    assert item.resubmission_count == 1
    assert item.history.filter(action="Request withdrawn").exists()


def test_reuse_pins_revision_without_mutating_approved_application(environment):
    first = approve(environment)
    pin = first.selected_submission_id
    second = milestone(environment, "m2")
    services.reuse_evidence(second, environment["applicant"])
    second.refresh_from_db()
    assert second.selected_submission_id == pin
    second, form, saved = services.save_review_form(
        second,
        environment["applicant"],
        data={**evidence_data(), "wasa_agency": "M/s ANB Solutions Private Limited"},
        submit=True,
    )
    assert saved, form.errors
    first.refresh_from_db()
    assert first.selected_submission_id == pin
    assert second.selected_submission.submission_number == 2
    assert second.selected_submission.revision == 1
    assert second.form_id == first.form_id


def test_multiple_evidence_append_remove_keeps_history(environment):
    uploads = files()
    uploads.setlist("supporting_evidence", [pdf("one.pdf"), pdf("two.pdf")])
    item, form, saved = services.save_review_form(
        milestone(environment),
        environment["applicant"],
        data=evidence_data(),
        files=uploads,
        submit=False,
    )
    assert saved, form.errors
    before = item.selected_submission
    removed = before.attachments.get(original_name="one.pdf")
    item, form, saved = services.save_review_form(
        item,
        environment["applicant"],
        data={
            **evidence_data(),
            "remove_files__supporting_evidence": [str(removed.pk)],
        },
        files=MultiValueDict(
            {"supporting_evidence": [pdf("three.pdf"), pdf("four.pdf")]},
        ),
        submit=True,
    )
    assert saved, form.errors
    assert set(
        item.selected_submission.attachments.filter(
            field_key="supporting_evidence",
        ).values_list("original_name", flat=True),
    ) == {"two.pdf", "three.pdf", "four.pdf"}
    assert set(
        before.attachments.filter(field_key="supporting_evidence").values_list(
            "original_name",
            flat=True,
        ),
    ) == {"one.pdf", "two.pdf"}


def test_stale_form_cannot_overwrite_teammate(environment):
    item = milestone(environment)
    services.save_review_form(
        item,
        environment["applicant"],
        data={"wasa_agency": evidence_data()["wasa_agency"]},
    )
    with pytest.raises(ValidationError, match="teammate"):
        services.save_review_form(
            item,
            environment["applicant"],
            data=evidence_data(),
            expected_revision="",
        )


def test_approved_track_selection_cannot_be_removed(environment):
    approve(environment)
    registration = environment["workspace"].product.review_items.get(
        kind="product_registration",
    )
    with pytest.raises(ValidationError, match="Approved milestones"):
        services.save_review_form(
            registration,
            environment["applicant"],
            data={**product_data(), "applied_milestones": ["HealthLocker:locker1"]},
            submit=True,
        )


def test_date_and_pdf_validation_and_required_documents():
    form = ExitEvidenceForm(data=evidence_data())
    assert not form.is_valid()
    assert "functional_report" in form.errors
    fake = SimpleUploadedFile(
        "fake.pdf",
        b"<html>not a pdf</html>",
        content_type="application/pdf",
    )
    form = ExitEvidenceForm(
        data=evidence_data(),
        files={"functional_report": fake, "functional_certificate": pdf()},
    )
    assert not form.is_valid()
    assert "Upload a PDF" in str(form.errors)
    data = evidence_data()
    form = ExitEvidenceForm(data={**data, "end_date": "2000-01-01"}, files=files())
    assert not form.is_valid()
    assert "end_date" in form.errors
    assert ExitEvidenceForm(
        data={"wasa_agency": evidence_data()["wasa_agency"]},
        draft=True,
    ).is_valid()


def test_phr_requires_m1_but_not_m3_and_locker_is_independent():
    assert ProductRegistrationForm(
        data={**product_data(), "applied_milestones": ["HIE-CM:m1", "PHR:phr1"]},
    ).is_valid()
    assert ProductRegistrationForm(
        data={**product_data(), "applied_milestones": ["HealthLocker:locker1"]},
    ).is_valid()
    assert not ProductRegistrationForm(
        data={**product_data(), "applied_milestones": ["PHR:phr1"]},
    ).is_valid()


def test_credentials_encrypted_audited_rate_limited_and_not_in_outcomes(
    environment,
    client,
):
    product = environment["workspace"].product
    credential = ProductCredential.objects.get(product=product)
    plain = credentials.cipher().decrypt(credential.encrypted_secret.encode()).decode()
    outcome = product.outcomes.get(outcome_type="sandbox_credentials")
    assert plain not in str(outcome.data)
    assert plain not in credential.encrypted_secret
    url = reverse("experiences:credentials", args=[environment["workspace"].reference])
    client.force_login(environment["applicant"])
    response = client.get(url)
    assert response.status_code == 200
    assert plain not in response.content.decode()
    response = client.post(url, {"intent": "reveal"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert plain in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert AuditEvent.objects.filter(action="Client secret revealed").count() == 1
    for _ in range(4):
        credentials.reveal(credential, environment["applicant"])
    with pytest.raises(ValidationError, match="Too many"):
        credentials.reveal(credential, environment["applicant"])
    client.force_login(environment["reviewer"])
    assert client.get(url).status_code == 403
    assert client.post(url, {"intent": "reveal"}).status_code == 403


def test_rotation_replaces_the_stored_secret(environment):
    product = environment["workspace"].product
    credential = ProductCredential.objects.get(product=product)
    first = credentials.reveal(credential, environment["applicant"])

    credentials.rotate(credential, environment["applicant"])

    credential.refresh_from_db()
    assert credentials.reveal(credential, environment["applicant"]) != first


def test_revoke_switches_every_system_off_and_closes_the_panel(
    environment,
    django_capture_on_commit_callbacks,
):
    product = environment["workspace"].product
    credential = ProductCredential.objects.get(product=product)

    with django_capture_on_commit_callbacks(execute=True):
        credentials.revoke(credential, environment["applicant"])

    credential.refresh_from_db()
    assert credential.status == "revoked"
    assert credential.encrypted_secret == ""
    assert set(
        ProvisionedResource.objects.filter(product=product).values_list(
            "state",
            flat=True,
        ),
    ) == {ProvisionedResourceState.DISABLED}
    with pytest.raises(ValidationError, match="no longer active"):
        credentials.reveal(credential, environment["applicant"])


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1"],
)
def test_callback_rejects_private_dns_results(address):
    with (
        patch(
            "ohc_experience.experiences.credentials.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
        ),
        pytest.raises(ValidationError, match="Private"),
    ):
        credentials.public_callback_target("https://callback.example.org/")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org",
        "https://user:pass@example.org",
        "https://example.org:8443",
        "https://example.org/#fragment",
    ],
)
def test_callback_rejects_unsafe_urls(url):
    with pytest.raises(ValidationError):
        credentials.public_callback_target(url)


def test_permission_boundaries_and_all_portal_pages_render(environment, client):
    workspace = environment["workspace"]
    approve(environment)
    item = submit(environment, "m2")
    services.assign_review(item, environment["admin"], environment["reviewer"])
    services.decide(
        item,
        environment["reviewer"],
        action="query",
        note="Explain evidence.",
    )
    client.force_login(environment["applicant"])
    paths = [
        reverse("experiences:products"),
        workspace.get_absolute_url(),
        reverse("experiences:organisation"),
        reverse("experiences:product-edit", args=[workspace.reference]),
        reverse("experiences:pending-queries"),
        reverse("experiences:events"),
        reverse("experiences:support"),
        reverse("experiences:submission", args=[item.pk, item.selected_submission_id]),
    ]
    paths += [
        reverse("experiences:track", args=[workspace.reference, code])
        for code in TRACK_MAP
    ]
    for path in paths:
        response = client.get(path, HTTP_HX_REQUEST="true")
        assert response.status_code == 200, path
        assert b'id="portal"' in response.content, path
    assert client.get(reverse("experiences:queue")).status_code == 403
    client.force_login(environment["outsider"])
    assert client.get(workspace.get_absolute_url()).status_code == 404
    file = item.selected_submission.attachments.first()
    assert (
        client.get(reverse("experiences:attachment", args=[file.pk])).status_code == 404
    )
    client.force_login(environment["reviewer"])
    for path in [
        reverse("experiences:assess-dashboard"),
        reverse("experiences:queue"),
        item.get_absolute_url(),
        item.get_absolute_url() + "?action=query&field=wasa_agency",
    ]:
        response = client.get(path)
        assert response.status_code == 200, path
    assert (
        client.get(reverse("experiences:attachment", args=[file.pk])).status_code == 200
    )


def test_historical_schema_is_rendered_after_form_definition_changes(
    environment,
    client,
):
    item = submit(environment)
    snapshot = item.selected_submission
    snapshot.field_schema = [
        {
            "key": "retired_field",
            "label": "Retired audit reference",
            "type": "CharField",
            "choices": [],
        },
    ]
    snapshot.data = {"retired_field": "audit-2025-001"}
    snapshot.save()
    client.force_login(environment["applicant"])
    response = client.get(
        reverse("experiences:submission", args=[item.pk, snapshot.pk]),
    )
    assert b"Retired audit reference" in response.content
    assert b"audit-2025-001" in response.content


def test_audit_events_cannot_be_changed_or_deleted(environment):
    event = AuditEvent.objects.first()
    for operation in [
        lambda: AuditEvent.objects.filter(pk=event.pk).update(action="Changed"),
        lambda: AuditEvent.objects.filter(pk=event.pk).delete(),
        event.save,
        event.delete,
    ]:
        with pytest.raises(ValidationError, match="append-only"):
            operation()


def test_sent_back_draft_retains_reason_and_decision_history(environment, client):
    item = submit(environment)
    services.assign_review(item, environment["admin"], environment["reviewer"])
    services.decide(
        item,
        environment["reviewer"],
        action="send_back",
        note="Revise scope.",
    )
    item, form, saved = services.save_review_form(
        item,
        environment["applicant"],
        data={"wasa_agency": "M/s A3S Tech & Company"},
    )
    assert saved, form.errors
    assert item.status == "sent_back"
    assert item.decision_note == "Revise scope."
    item, form, saved = services.save_review_form(
        item,
        environment["applicant"],
        data=evidence_data(),
        submit=True,
    )
    assert saved, form.errors
    assert item.status == "in_review"
    client.force_login(environment["reviewer"])
    response = client.get(reverse("experiences:assess-dashboard"))
    assert sum(week["sent_back"] for week in response.context["weeks"]) == 1
    assert response.context["median_days"] is not None


def test_track_filter_respects_which_track_applied_for_shared_m1(environment, client):
    item = submit(environment)
    workspace = environment["workspace"]
    workspace.applied_milestones = ["HIE-CM:m1"]
    workspace.save()
    client.force_login(environment["reviewer"])
    url = reverse("experiences:queue")
    assert item in client.get(url, {"item": "HIE-CM"}).context["page"]
    assert item not in client.get(url, {"item": "PHR"}).context["page"]
    workspace.applied_milestones.append("PHR:phr1")
    workspace.save()
    assert item in client.get(url, {"item": "PHR"}).context["page"]


def test_the_queue_lists_newest_first_unless_asked_for_oldest(environment, client):
    older = submit(environment, "m1")
    newer = submit(environment, "locker1")
    ReviewItem.objects.filter(pk=older.pk).update(
        submitted_at=timezone.now() - timedelta(days=2),
    )
    client.force_login(environment["reviewer"])

    def listed(**params):
        page = client.get(reverse("experiences:queue"), params).context["page"]
        return [item for item in page if item in (older, newer)]

    assert listed() == [newer, older]
    assert listed(sort="oldest") == [older, newer]


def test_the_item_filter_reaches_requests_outside_any_track(environment, client):
    milestone_item = submit(environment)
    client.force_login(environment["reviewer"])

    def listed(item):
        return client.get(
            reverse("experiences:queue"),
            {"item": item, "scope": "all"},
        ).context["page"]

    assert {entry.kind for entry in listed("organisation_verification")} == {
        ReviewItem.Kind.ORGANISATION,
    }
    assert milestone_item in listed("HIE-CM")
    assert milestone_item not in listed("organisation_verification")


def test_product_registrations_are_records_not_queue_requests(environment, client):
    registration = environment["workspace"].product.review_items.get(
        kind="product_registration",
    )
    client.force_login(environment["reviewer"])

    queue = client.get(reverse("experiences:queue")).context["page"]
    organisation = client.get(
        reverse("experiences:organization-detail", args=[environment["org"].slug]),
    ).context["review_requests"]
    record = client.get(registration.get_absolute_url())

    assert registration not in queue
    assert registration not in organisation
    assert record.status_code == 200
    assert "data-decision-form" not in record.content.decode()


def test_the_type_tabs_only_offer_what_the_item_filter_can_match(environment, client):
    client.force_login(environment["reviewer"])

    def tabs(**params):
        response = client.get(reverse("experiences:queue"), params)
        return [tab["label"] for tab in response.context["queue_tabs"]]

    wasa = "WASA certification review"
    requests = ["Organisation verification", wasa]
    assert tabs() == ["All", "Mine", *requests, "Application request"]
    assert tabs(item="organisation_verification") == [
        "All",
        "Mine",
        "Organisation verification",
    ]
    assert tabs(item="certification") == ["All", "Mine", wasa]
    assert tabs(item="UHI") == ["All", "Mine", "Application request"]

    stale = client.get(
        reverse("experiences:queue"),
        {"item": "UHI", "kind": "organisation_verification"},
    )
    assert stale.context["filters"]["kind"] == ""


def test_the_review_page_holds_decisions_until_prerequisites_are_approved(
    environment,
    client,
):
    m1 = submit(environment)
    m2 = submit(environment, "m2")
    client.force_login(environment["reviewer"])

    response = client.get(m2.get_absolute_url())
    html = response.content.decode()

    assert response.context["decision_action"] == "query"
    assert "Approve or send back this request once M1 - ABHA and identity" in html
    assert 'data-decision-blocked="true"' in html
    assert re.search(r'value="approve"\s+disabled', html)
    assert re.search(r'value="send_back"\s+disabled', html)
    assert f'href="{m1.get_absolute_url()}">M1 - ABHA and identity</a>' in html
    assert "waiting on prerequisites" in html

    approve_submitted(environment)
    html = client.get(m2.get_absolute_url()).content.decode()

    assert 'id="decision-hold"' not in html
    assert not re.search(r'value="approve"\s+disabled', html)


def test_a_waiting_recorded_request_offers_reviewers_no_decision(environment, client):
    submit(environment)
    uhi = submit(environment, "uhi1")
    client.force_login(environment["reviewer"])

    html = client.get(uhi.get_absolute_url()).content.decode()

    assert 'id="recording-hold"' in html
    assert "recorded automatically once M1 - ABHA and identity is approved" in html
    assert "data-decision-form" not in html


def test_pending_queries_follow_the_selected_product(environment, client):
    item = submit(environment)
    services.assign_review(item, environment["admin"], environment["reviewer"])
    services.decide(
        item,
        environment["reviewer"],
        action="query",
        note="Explain evidence.",
    )
    other, form = services.register_product(
        environment["org"],
        environment["applicant"],
        data=product_data("Second product"),
    )
    assert other, form.errors
    client.force_login(environment["applicant"])
    url = reverse("experiences:pending-queries")

    here = client.get(url, {"product": environment["workspace"].reference})
    assert item in here.context["items"]
    assert here.context["query_count"] == 1

    elsewhere = client.get(url, {"product": other.reference})
    assert item not in elsewhere.context["items"]
    assert elsewhere.context["query_count"] == 0


def test_removed_organisation_urls_cannot_bypass_review(environment, client):
    org = environment["org"]
    client.force_login(environment["applicant"])
    for url in ["/settings/organisation/", "/onboarding/"]:
        assert client.post(url, {"name": "Bypassed review"}).status_code == 404
    client.force_login(environment["reviewer"])
    response = client.post(
        f"/ohc/organisations/{org.slug}/verification/",
        {"verification_status": "sent_back"},
    )
    assert response.status_code == 404
    org.refresh_from_db()
    assert org.is_verified
    assert org.name != "Bypassed review"


def test_support_members_cannot_reply_or_withdraw(environment, client):
    supporter = UserFactory()
    Membership.objects.create(
        user=supporter,
        organisation=environment["org"],
        role="support",
    )
    item = submit(environment)
    services.assign_review(item, environment["admin"], environment["reviewer"])
    services.decide(
        item,
        environment["reviewer"],
        action="query",
        note="Clarify scope.",
    )
    client.force_login(supporter)
    response = client.get(
        reverse(
            "experiences:track",
            args=[environment["workspace"].reference, "HIE-CM"],
        ),
    )
    assert response.status_code == 200
    assert b"Withdraw request</button>" not in response.content
    assert b"Send reply</button>" not in response.content
    with pytest.raises(PermissionDenied):
        services.withdraw(item, supporter)
    with pytest.raises(PermissionDenied):
        services.reply_query(item.queries.get(), supporter, "Reply")


def test_review_mutation_requires_csrf(environment):
    item = submit(environment)
    client = Client(enforce_csrf_checks=True)
    client.force_login(environment["applicant"])
    assert (
        client.post(reverse("experiences:withdraw", args=[item.pk])).status_code == 403
    )
    item.refresh_from_db()
    assert item.pending


def _pending_organisation(environment):
    """An organisation back in verification, with a product registered anyway."""
    org = environment["org"]
    Organisation.objects.filter(pk=org.pk).update(verification_status="pending")
    org.refresh_from_db()
    workspace, form = services.register_product(
        org,
        environment["applicant"],
        data=product_data("Second product"),
    )
    assert workspace, form.errors
    return org, workspace.product


def test_registering_provisions_before_the_organisation_is_verified(environment):
    org, product = _pending_organisation(environment)

    assert not org.is_verified
    assert not awaiting_provisioning(product)
    provision_inline(product)
    assert ProductCredential.objects.get(product=product).status == "active"


def test_verification_leaves_provisioning_alone(environment):
    """Registration starts every chain, so re-approval must not open another."""
    first = environment["workspace"].product
    org, second = _pending_organisation(environment)
    runs_before = {
        product.pk: product.provisioning_runs.count() for product in (first, second)
    }
    item = org.review_items.get(kind="organisation_verification")
    services.save_review_form(
        item,
        environment["applicant"],
        data=organisation_data(),
        files={"supporting_document": pdf()},
        submit=True,
    )
    services.assign_review(item, environment["admin"], environment["reviewer"])

    services.decide(item, environment["reviewer"], action="approve", note="Verified.")

    assert {
        product.pk: product.provisioning_runs.count() for product in (first, second)
    } == runs_before


def test_a_reviewer_can_start_a_product_that_was_never_provisioned(
    environment,
    client,
):
    """Registered while provisioning still waited for verification."""
    _org, product = _pending_organisation(environment)
    ProvisioningRun.objects.filter(product=product).delete()
    item = product.review_items.get(kind="product_registration")
    client.force_login(environment["reviewer"])

    html = client.get(reverse("experiences:review", args=[item.pk])).content.decode()
    response = client.post(
        reverse("experiences:review", args=[item.pk]),
        {"intent": "retry_provisioning"},
        follow=True,
    )

    assert "Start provisioning</button>" in html
    assert "Provisioning started." in response.content.decode()
    assert product.provisioning_runs.filter(started_by=environment["reviewer"]).exists()


def _failed_registration(environment):
    """A product whose chain died, and the review item a reviewer sees it on."""
    fail_next(ExternalSystem.KEYCLOAK, "create_client", retryable=False)
    _org, product = _pending_organisation(environment)
    provision_inline(product)
    return product, product.review_items.get(kind="product_registration")


def test_a_reviewer_can_restart_a_failed_chain(environment, client):
    product, item = _failed_registration(environment)
    assert provisioning_can_be_retried(product)
    client.force_login(environment["reviewer"])

    response = client.post(
        reverse("experiences:review", args=[item.pk]),
        {"intent": "retry_provisioning"},
        follow=True,
    )

    assert response.status_code == 200
    assert product.provisioning_runs.filter(started_by=environment["reviewer"]).exists()


def test_the_retry_is_not_offered_once_there_is_nothing_to_retry(environment, client):
    registration = environment["workspace"].product.review_items.get(
        kind="product_registration",
    )
    client.force_login(environment["reviewer"])

    html = client.get(
        reverse("experiences:review", args=[registration.pk]),
    ).content.decode()

    assert "retry_provisioning" not in html


def test_a_reviewer_cannot_force_a_retry_the_ledger_does_not_want(
    environment,
    client,
):
    """The button is hidden on a healthy product; posting the intent anyway fails."""
    registration = environment["workspace"].product.review_items.get(
        kind="product_registration",
    )
    client.force_login(environment["reviewer"])

    response = client.post(
        reverse("experiences:review", args=[registration.pk]),
        {"intent": "retry_provisioning"},
    )

    assert response.status_code == 403


def test_an_integrator_cannot_restart_a_chain(environment, client):
    _product, item = _failed_registration(environment)
    client.force_login(environment["applicant"])

    response = client.post(
        reverse("experiences:review", args=[item.pk]),
        {"intent": "retry_provisioning"},
    )

    assert response.status_code == 403


def test_a_rotation_that_cannot_be_stored_says_so(environment):
    credential = ProductCredential.objects.get(product=environment["workspace"].product)
    before = stored_secret(environment["workspace"].product)

    with (
        patch.object(
            ProductCredential,
            "save",
            side_effect=DatabaseError("connection lost"),
        ),
        pytest.raises(ValidationError, match="Rotate again"),
    ):
        credentials.rotate(credential, environment["applicant"])

    credential.refresh_from_db()
    assert stored_secret(environment["workspace"].product) == before


def test_pdf_validation_keeps_upload_readable_without_network_access():
    document = pdf()
    with patch("socket.create_connection") as connect:
        uploads.validate_pdf(document)
        connect.assert_not_called()
    assert document.tell() == 0
    assert document.read() == b"%PDF-1.4\n%%EOF"


def test_logo_is_an_optional_link():
    field = OrganisationForm().fields["logo"]
    assert field.clean("") == ""
    assert field.clean("https://example.org/logo.png") == "https://example.org/logo.png"
    with pytest.raises(ValidationError, match="valid URL"):
        field.clean("not a link")


def test_an_uploaded_logo_stays_with_its_revision(environment):
    item = environment["org"].review_items.get(kind="organisation_verification")
    uploaded = item.selected_submission
    logo = uploaded.attachments.create(
        field_key="logo",
        file="experience-attachments/logo.png",
        original_name="logo.png",
        size=1,
        uploaded_by=environment["applicant"],
    )
    uploaded.data["logo"] = {"attachment_id": logo.pk, "name": "logo.png", "size": 1}
    uploaded.save(update_fields=["data"])

    assert services.build_form(item)["logo"].value() is None

    item, form, saved = services.save_review_form(
        item,
        environment["applicant"],
        data={**organisation_data(), "logo": "https://example.org/logo.png"},
        submit=True,
    )
    assert saved, form.errors
    assert item.selected_submission.data["logo"] == "https://example.org/logo.png"
    assert not item.selected_submission.attachments.filter(field_key="logo").exists()
    assert uploaded.attachments.get(field_key="logo", is_current=True) == logo


def test_oversized_document_is_rejected():
    document = pdf()
    document.size = uploads.MAX_UPLOAD_BYTES + 1
    with pytest.raises(ValidationError, match="10 MB"):
        uploads.validate_pdf(document)
