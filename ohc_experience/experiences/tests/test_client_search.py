# ruff: noqa: F811, PLR2004
import base64
import json
import time

import pytest

from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.integrations.models import ApiCaller
from ohc_experience.integrations.models import hash_key
from ohc_experience.integrations.models import new_key

pytestmark = pytest.mark.django_db

URL = "/api/sandbox/v1/search/clients"


def token(**claims):
    """A JWT as legacy accepted one: decodable, with any signature at all."""

    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256', 'typ': 'JWT'})}.{part(claims)}.not-checked"


def live_token():
    return token(
        iss="https://gateway.example/realms/central",
        azp="hiecm-service",
        exp=int(time.time()) + 300,
    )


def search(client, client_ids, *, authorization=None, path=URL):
    return client.post(
        path,
        data=json.dumps({"clientIds": client_ids}),
        content_type="application/json",
        headers={
            "Authorization": live_token() if authorization is None else authorization,
            "REQUEST-ID": "6f1f2a2e-5d5b-4a51-9a35-6a1f6c9d1c11",
            "TIMESTAMP": "2026-10-22T10:00:00.000Z",
        },
    )


def sandbox_id(product):
    return ProductCredential.objects.get(product=product).client_id


def caller_with_key(*, active=True):
    key = new_key()
    caller = ApiCaller.objects.create(
        name="HIE-CM team",
        key_hash=hash_key(key),
        active=active,
    )
    return caller, key


def test_a_sandbox_client_id_answers_with_its_approved_milestones(
    client,
    environment,
):
    product = environment["product"]
    approve(environment, "m1")
    approve(environment, "m4")

    response = search(client, [sandbox_id(product)])

    assert response.status_code == 200
    assert response.json() == [
        {"name": product.name, "milestones": "M1,M4", "clientId": sandbox_id(product)},
    ]


def test_a_production_client_id_finds_the_product_too(client, environment):
    product = environment["product"]
    approve(environment)
    Product.objects.filter(pk=product.pk).update(production_client_id="PROD-HMIS-1")

    response = search(client, ["PROD-HMIS-1"])

    assert response.json() == [
        {"name": product.name, "milestones": "M1", "clientId": "PROD-HMIS-1"},
    ]


def test_asked_by_both_ids_a_product_answers_once_by_its_sandbox_id(
    client,
    environment,
):
    product = environment["product"]
    approve(environment)
    Product.objects.filter(pk=product.pk).update(production_client_id="PROD-HMIS-1")

    response = search(client, ["PROD-HMIS-1", sandbox_id(product)])

    assert response.json() == [
        {"name": product.name, "milestones": "M1", "clientId": sandbox_id(product)},
    ]


def test_unknown_client_ids_are_left_out(client, environment):
    product = environment["product"]
    approve(environment)

    response = search(client, ["SBX_UNKNOWN", sandbox_id(product)])

    assert [row["clientId"] for row in response.json()] == [sandbox_id(product)]
    assert search(client, ["SBX_UNKNOWN"]).json() == []


def test_milestones_under_review_are_not_listed(client, environment):
    """With nothing approved the field is left out, as legacy left out a null."""
    product = environment["product"]
    submit(environment, "m1")

    response = search(client, [sandbox_id(product)])

    assert response.json() == [{"name": product.name, "clientId": sandbox_id(product)}]


@pytest.mark.parametrize("body", [{}, {"clientIds": []}, {"clientIds": "SBX_1"}])
def test_a_request_without_client_ids_is_refused(client, environment, body):
    response = client.post(
        URL,
        data=json.dumps(body),
        content_type="application/json",
        headers={"Authorization": live_token()},
    )

    assert response.status_code == 400
    assert response.json() == {
        "message": "clientIds must be a non-empty list of client IDs.",
    }


def test_the_path_takes_a_trailing_slash_as_legacy_did(client, environment):
    product = environment["product"]

    response = search(client, [sandbox_id(product)], path=f"{URL}/")

    assert response.status_code == 200


def test_only_post_is_answered(client):
    assert client.get(URL, headers={"Authorization": live_token()}).status_code == 405


def test_a_bearer_prefix_is_accepted(client, environment):
    product = environment["product"]

    response = search(
        client,
        [sandbox_id(product)],
        authorization=f"Bearer {live_token()}",
    )

    assert response.status_code == 200


def test_an_expired_token_is_refused_as_expired(client, environment):
    response = search(
        client,
        [sandbox_id(environment["product"])],
        authorization=token(iss="https://gateway.example", exp=int(time.time()) - 1),
    )

    assert response.status_code == 401
    assert response.json() == {"message": "Token expired."}


@pytest.mark.parametrize(
    "authorization",
    [
        "",
        "not-a-token",
        token(iss="https://gateway.example"),
        "e30.bm90LWpzb24.sig",
    ],
    ids=["missing", "not-a-jwt", "no-expiry", "claims-not-json"],
)
def test_anything_but_a_token_or_a_key_is_denied(
    client,
    environment,
    authorization,
):
    response = search(
        client,
        [sandbox_id(environment["product"])],
        authorization=authorization,
    )

    assert response.status_code == 401
    assert response.json() == {"message": "Access denied."}


def test_an_api_key_is_accepted_and_its_use_recorded(client, environment):
    product = environment["product"]
    caller, key = caller_with_key()

    response = search(client, [sandbox_id(product)], authorization=f"Bearer {key}")

    assert response.status_code == 200
    caller.refresh_from_db()
    assert caller.last_used_at is not None


@pytest.mark.parametrize("active", [False, True], ids=["deactivated", "unknown"])
def test_a_key_that_does_not_open_is_refused_without_trying_it_as_a_token(
    client,
    environment,
    active,
):
    _caller, key = caller_with_key(active=active)
    if active:
        key = f"{key}x"

    response = search(
        client,
        [sandbox_id(environment["product"])],
        authorization=key,
    )

    assert response.status_code == 401
    assert response.json() == {"message": "Access denied."}


def test_each_call_logs_who_made_it_without_the_credential(
    client,
    environment,
    caplog,
):
    product = environment["product"]
    caller, key = caller_with_key()
    legacy = live_token()

    with caplog.at_level("INFO", logger="ohc_experience.experiences.client_search"):
        search(client, [sandbox_id(product)], authorization=legacy)
        search(client, [sandbox_id(product), "SBX_UNKNOWN"], authorization=key)

    token_line, key_line = (record.getMessage() for record in caplog.records)
    assert "iss=https://gateway.example/realms/central" in token_line
    assert "azp=hiecm-service" in token_line
    assert "1 IDs asked, 1 found" in token_line
    assert f"API caller {caller.pk} (HIE-CM team)" in key_line
    assert "2 IDs asked, 1 found" in key_line
    assert legacy not in caplog.text
    assert key not in caplog.text
