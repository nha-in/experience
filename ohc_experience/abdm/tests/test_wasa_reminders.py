# ruff: noqa: F811, PLR2004
import pytest
from django.utils import timezone

from ohc_experience.abdm.tests.test_wasa_lifecycle import certificate_data
from ohc_experience.abdm.tests.test_wasa_lifecycle import decide
from ohc_experience.abdm.tests.test_wasa_lifecycle import request_milestone
from ohc_experience.abdm.tests.test_wasa_lifecycle import request_renewal
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.wasa import current_wasa
from ohc_experience.core.mail import QUEUED_GLOBAL_EMAIL_BACKEND
from ohc_experience.core.mail.templates import APPROVED_TEMPLATE_IDS
from ohc_experience.experiences.models import Notification
from ohc_experience.experiences.tasks import remind_expiring_outcomes

pytestmark = pytest.mark.django_db


def approve_certificate(environment, *, expires_in):
    item = request_milestone(
        environment,
        certificate=certificate_data(expires_in=expires_in),
    )
    decide(environment, item)
    # The approval mails the applicant too; only the reminders are under test.
    Notification.objects.all().delete()
    return current_wasa(environment["workspace"].product)


def reminders():
    return Notification.objects.filter(body__contains="Renew it before then")


def test_certificate_nearing_expiry_warns_the_organisation(environment):
    approve_certificate(environment, expires_in=14)

    remind_expiring_outcomes()

    notification = reminders().get()
    assert notification.recipient == environment["applicant"].email
    assert notification.subject.endswith("— 14 days left")
    assert "— 14 days left." in notification.body
    assert "/certification/" in notification.body


def test_a_comfortable_certificate_is_left_alone(environment):
    approve_certificate(environment, expires_in=90)

    remind_expiring_outcomes()

    assert not reminders().exists()


def test_each_window_warns_once(environment):
    outcome = approve_certificate(environment, expires_in=20)

    remind_expiring_outcomes()
    remind_expiring_outcomes()

    assert reminders().count() == 1
    outcome.refresh_from_db()
    assert outcome.metadata["expiry_reminders"] == [30]


def test_a_closer_window_warns_again(environment):
    outcome = approve_certificate(environment, expires_in=20)
    remind_expiring_outcomes()

    outcome.valid_until = timezone.localdate() + timezone.timedelta(days=6)
    outcome.save(update_fields=["valid_until"])
    remind_expiring_outcomes()

    assert reminders().count() == 2
    assert "— 6 days left" in reminders().latest("created_at").subject


def test_a_late_first_run_owes_one_warning_stating_the_real_days_left(environment):
    outcome = approve_certificate(environment, expires_in=3)

    remind_expiring_outcomes()

    notification = reminders().get()
    assert "— 3 days left" in notification.subject
    outcome.refresh_from_db()
    assert outcome.metadata["expiry_reminders"] == [30, 15, 7]


def test_an_expired_certificate_is_not_chased(environment):
    outcome = approve_certificate(environment, expires_in=5)
    outcome.valid_until = timezone.localdate() - timezone.timedelta(days=1)
    outcome.save(update_fields=["valid_until"])

    remind_expiring_outcomes()

    assert not reminders().exists()


def test_a_renewal_silences_the_certificate_it_replaces(environment):
    approve_certificate(environment, expires_in=10)
    item = request_renewal(
        environment,
        certificate=certificate_data(expires_in=300),
    )
    decide(environment, item)

    remind_expiring_outcomes()

    assert not reminders().exists()


def test_the_renewal_warning_carries_its_own_template(environment, settings):
    """NHA registered this warning separately; it must not ride the event
    notice every unmapped message falls back to."""
    settings.EMAIL_BACKEND = QUEUED_GLOBAL_EMAIL_BACKEND
    approve_certificate(environment, expires_in=14)

    remind_expiring_outcomes()

    assert reminders().get().template_id == APPROVED_TEMPLATE_IDS["certificate_expiry"]
