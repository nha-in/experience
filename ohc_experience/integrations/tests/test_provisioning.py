"""What registering a product causes: the chain, the ledger, the attempt record."""

from __future__ import annotations

import pytest
from celery.exceptions import Retry
from django.test import override_settings

from ohc_experience.experiences.models import Notification
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.experiences.secrets import cipher
from ohc_experience.integrations.correlation import set_correlation_id
from ohc_experience.integrations.local import always_fail
from ohc_experience.integrations.local import fail_next
from ohc_experience.integrations.models import ProvisionedResource
from ohc_experience.integrations.models import ProvisionedResourceState
from ohc_experience.integrations.models import ProvisionedSystem
from ohc_experience.integrations.models import ProvisioningRun
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.secret_ref import discard_secret
from ohc_experience.integrations.tasks import _external_name
from ohc_experience.integrations.tasks import complete_provisioning
from ohc_experience.integrations.tasks import provision_keycloak
from ohc_experience.integrations.tasks import provision_wso2

pytestmark = pytest.mark.django_db

CHAINED_SYSTEMS = {ProvisionedSystem.KEYCLOAK, ProvisionedSystem.WSO2}
BACKOFF_SEQUENCE = [120, 240, 480, 900]


def _systems(product) -> set[str]:
    return set(
        ProvisionedResource.objects.filter(
            product=product,
            state=ProvisionedResourceState.ACTIVE,
        ).values_list("system", flat=True),
    )


def _refs(product) -> dict[int, str]:
    return {row.pk: row.external_ref for row in product.provisioned_resources.all()}


def _run(product) -> ProvisioningRun:
    return product.provisioning_runs.order_by("-started_at").first()


def _stored_secret(product) -> str:
    credential = ProductCredential.objects.get(product=product)
    return cipher().decrypt(credential.encrypted_secret.encode()).decode()


# ── The seam: registration reaches the chain ─────────────────────────────────


def test_registration_provisions_the_chained_systems(provision):
    """Not the bridge: it waits on a callback URL."""
    product = provision()

    assert _systems(product) == CHAINED_SYSTEMS


def test_registration_opens_an_attempt_record_and_closes_it_ready(provision):
    """Where "is this provisioned?" is answered. The product exists either way."""
    product = provision()

    run = _run(product)
    assert run.status == ProvisioningRun.Status.READY
    assert run.finished_at is not None
    assert not run.error


def test_completion_publishes_the_credential_the_panel_reads(provision):
    product = provision()

    credential = ProductCredential.objects.get(product=product)
    assert credential.status == "active"
    assert credential.client_id == (
        ProvisionedResource.objects.get(
            product=product,
            system=ProvisionedSystem.KEYCLOAK,
        ).public_ref
    )
    assert credential.gateway_url


def test_completion_tells_the_integrators_without_carrying_the_secret(provision):
    product = provision()
    secret = _stored_secret(product)

    notifications = Notification.objects.all()

    assert notifications
    assert all(secret not in notification.body for notification in notifications)
    assert any("credentials available" in n.subject for n in notifications)


def test_nothing_is_provisioned_until_the_registration_commits(product):
    """The chain is scheduled on commit, so a rolled-back registration
    cannot have asked Keycloak for anything."""
    assert _run(product).status == ProvisioningRun.Status.RUNNING
    assert not ProvisionedResource.objects.filter(product=product).exists()


# ── The ledger ───────────────────────────────────────────────────────────────


def test_the_keycloak_row_carries_both_references(provision):
    product = provision()

    row = ProvisionedResource.objects.get(
        product=product,
        system=ProvisionedSystem.KEYCLOAK,
    )
    assert row.external_ref
    assert row.public_ref
    assert row.external_ref != row.public_ref


def test_provisioning_registers_no_bridge(provision):
    product = provision()

    assert not product.provisioned_resources.filter(
        system=ProvisionedSystem.HIECM,
    ).exists()


def test_re_running_the_chain_creates_nothing_new(provision):
    """The ledger, not the caller, is what makes a retry safe."""
    product = provision()
    before = _refs(product)

    provision()

    assert _refs(product) == before


# ── Failure ──────────────────────────────────────────────────────────────────


def test_a_failing_step_closes_the_attempt_with_the_reason(provision):
    fail_next(ExternalSystem.WSO2, "create_application", retryable=False)

    product = provision()

    run = _run(product)
    assert run.status == ProvisioningRun.Status.FAILED
    assert "WSO2" in run.error


def test_a_failed_step_stops_the_rest_of_the_chain(provision):
    fail_next(ExternalSystem.KEYCLOAK, "create_client", retryable=False)

    product = provision()

    assert _systems(product) == set()
    assert _run(product).status == ProvisioningRun.Status.FAILED


def test_a_failed_chain_publishes_no_credential(provision):
    fail_next(ExternalSystem.KEYCLOAK, "create_client", retryable=False)

    product = provision()

    assert not ProductCredential.objects.filter(product=product).exists()


def test_a_missing_api_name_list_fails_without_retrying(provision):
    """Configuration is not a transient fault, so it must not burn five attempts."""
    with override_settings(WSO2_API_IDS={"abdm": ()}):
        product = provision()

    # And the bridge is not built: a closed attempt stops the links after it.
    assert _systems(product) == {ProvisionedSystem.KEYCLOAK}
    assert "CONFIG_ERROR" in _run(product).error


def test_a_retryable_failure_retries_instead_of_closing_the_attempt(product):
    """A transient fault must leave the attempt open, or the retry lands in a
    run that already reads FAILED."""
    always_fail(ExternalSystem.KEYCLOAK, code="REALM_DOWN", retryable=True)

    with pytest.raises(Retry):
        provision_keycloak.delay(product.pk)

    assert _run(product).status == ProvisioningRun.Status.RUNNING
    assert not ProvisionedResource.objects.filter(product=product).exists()


def test_backoff_doubles_and_is_capped():
    from ohc_experience.integrations.tasks import _backoff  # noqa: PLC0415

    with override_settings(
        PROVISIONING_RETRY_BACKOFF_SECONDS=120,
        PROVISIONING_RETRY_BACKOFF_MAX_SECONDS=900,
    ):
        assert [_backoff(n) for n in range(4)] == BACKOFF_SEQUENCE


def test_completion_refuses_an_incomplete_ledger(product):
    """READY is a claim about three systems, not about the chain running."""
    complete_provisioning.delay(product.pk)

    run = _run(product)
    assert run.status == ProvisioningRun.Status.FAILED
    assert "incomplete ledger" in run.error
    assert not ProductCredential.objects.filter(product=product).exists()


def test_a_failure_to_publish_closes_the_attempt(product):
    """Both live systems and no credential is still a failure, not a run in flight."""
    provision_keycloak.delay(product.pk)
    provision_wso2.delay(product.pk)
    client = ProvisionedResource.objects.get(
        product=product,
        system=ProvisionedSystem.KEYCLOAK,
    )
    discard_secret(client.secret_ref)
    always_fail(ExternalSystem.KEYCLOAK, code="REALM_DOWN", retryable=False)

    complete_provisioning.delay(product.pk)

    run = _run(product)
    assert run.status == ProvisioningRun.Status.FAILED
    assert "REALM_DOWN" in run.error
    assert not ProductCredential.objects.filter(product=product).exists()


def test_a_failed_chain_records_the_failure_once(provision):
    """Completion sees the same short ledger the failed step already explained."""
    fail_next(ExternalSystem.WSO2, "create_application", retryable=False)

    product = provision()

    assert product.audit_events.filter(action="Provisioning failed").count() == 1
    assert not product.audit_events.filter(action="Provisioning incomplete").exists()


def test_a_re_run_finishes_only_the_missing_system(provision):
    """A retry is the same call: every ACTIVE system is skipped."""
    fail_next(ExternalSystem.WSO2, "create_application", retryable=False)
    product = provision()
    assert _systems(product) == {ProvisionedSystem.KEYCLOAK}
    before = _refs(product)

    ProvisioningRun.objects.create(product=product)
    provision()

    assert _systems(product) == CHAINED_SYSTEMS
    assert before.items() <= _refs(product).items()


# ── Secrets ──────────────────────────────────────────────────────────────────


def test_the_ledger_holds_no_secret_and_the_credential_only_a_ciphertext(provision):
    product = provision()
    secret = _stored_secret(product)

    rows = ProvisionedResource.objects.filter(product=product)
    assert all(secret not in str(row.__dict__) for row in rows)
    assert all(not row.secret_ref for row in rows)
    assert secret not in ProductCredential.objects.get(product=product).encrypted_secret


def test_a_parked_secret_that_aged_out_is_re_minted_at_publication(product):
    """The chain can finish after the short hand-off TTL passed."""
    provision_keycloak.delay(product.pk)
    client = ProvisionedResource.objects.get(
        product=product,
        system=ProvisionedSystem.KEYCLOAK,
    )
    discard_secret(client.secret_ref)
    provision_wso2.delay(product.pk)

    complete_provisioning.delay(product.pk)

    assert _stored_secret(product)


def test_an_expired_parked_secret_is_re_minted_rather_than_dead_ending(product):
    """The TTL is shorter than the chain's retry budget, so WSO2 can arrive at a
    step whose secret has aged out while the Keycloak row reads ACTIVE."""
    provision_keycloak.delay(product.pk)
    client = ProvisionedResource.objects.get(
        product=product,
        system=ProvisionedSystem.KEYCLOAK,
    )
    stale = client.secret_ref
    discard_secret(stale)

    provision_wso2.delay(product.pk)

    client.refresh_from_db()
    assert client.secret_ref != stale
    assert ProvisionedSystem.WSO2 in _systems(product)


# ── Correlation ──────────────────────────────────────────────────────────────


def test_the_chain_carries_the_id_the_registration_was_made_under(product):
    """One id ties the attempt record to the log lines the adapters wrote."""
    assert _run(product).correlation_id


def test_a_later_run_records_its_own_id(product):
    set_correlation_id("second-run-id")

    ProvisioningRun.objects.create(product=product, correlation_id="second-run-id")

    assert _run(product).correlation_id == "second-run-id"


# ── The name that goes out ───────────────────────────────────────────────────


def test_punctuation_is_stripped_before_the_name_reaches_keycloak(product):
    product.organisation.legal_name = "Acme (Health) Pvt. Ltd."
    product.organisation.save()

    assert _external_name(product) == "Acme Health Pvt Ltd"


def test_a_name_of_pure_punctuation_falls_back_to_the_reference(product):
    product.organisation.name = "!!!"
    product.organisation.legal_name = "!!!"
    product.organisation.save()

    assert _external_name(product) == product.workspace.reference.replace("-", " ")
