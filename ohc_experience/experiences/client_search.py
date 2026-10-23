"""Product names and approved milestones by client ID, for other ABDM services.

Legacy's POST /api/sandbox/v1/search/clients, kept to its contract so callers
only change the host. A caller sends an API key of its own or, as legacy
accepted, any unexpired JWT: legacy never checked a token's signature or issuer.
Each call logs who made it, so the teams still on a token can be found.
"""

import base64
import binascii
import json
import logging
import time
from http import HTTPStatus

from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ohc_experience.integrations.models import KEY_PREFIX
from ohc_experience.integrations.models import ApiCaller

from .models import Product
from .production import approved_codes

logger = logging.getLogger(__name__)

EXPIRED = "Token expired."
ACCESS_DENIED = "Access denied."
NO_CLIENT_IDS = "clientIds must be a non-empty list of client IDs."


def _refused(status, message):
    return JsonResponse({"message": message}, status=status)


def _key_or_token(request):
    """The Authorization header with any Bearer prefix taken off."""
    scheme, _, rest = request.headers.get("Authorization", "").strip().partition(" ")
    return rest.strip() if scheme.lower() == "bearer" else scheme


def legacy_token_claims(token):
    """The claims of a well-formed JWT with an expiry, expired or not, or None."""
    parts = token.split(".")
    if len(parts) != 3:  # noqa: PLR2004
        return None
    try:
        header, claims = (
            json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            for part in parts[:2]
        )
    except ValueError, binascii.Error:
        return None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        return None
    expires = claims.get("exp")
    if not isinstance(expires, int | float) or isinstance(expires, bool):
        return None
    return claims


def _rows(client_ids):
    asked = set(client_ids)
    products = list(
        Product.objects.filter(
            Q(credential__client_id__in=asked) | Q(production_client_id__in=asked),
        ).select_related("credential"),
    )
    codes = approved_codes(products)
    rows = []
    for product in products:
        credential = getattr(product, "credential", None)
        sandbox_id = credential.client_id if credential else ""
        client_id = sandbox_id if sandbox_id in asked else product.production_client_id
        row = {"name": product.name, "clientId": client_id}
        # Legacy left the field out for a product with nothing approved.
        if codes[product.pk]:
            row["milestones"] = ",".join(codes[product.pk])
        rows.append(row)
    return rows


@csrf_exempt
@require_POST
def search_clients(request):
    presented = _key_or_token(request)
    if presented.startswith(KEY_PREFIX):
        caller = ApiCaller.for_key(presented)
        if caller is None:
            logger.warning("Client search refused: unknown or deactivated API key")
            return _refused(HTTPStatus.UNAUTHORIZED, ACCESS_DENIED)
        ApiCaller.objects.filter(pk=caller.pk).update(last_used_at=timezone.now())
        who = f"API caller {caller.pk} ({caller.name})"
    else:
        claims = legacy_token_claims(presented)
        if claims is None:
            logger.warning("Client search refused: no API key or token")
            return _refused(HTTPStatus.UNAUTHORIZED, ACCESS_DENIED)
        who = f"legacy token iss={claims.get('iss')} azp={claims.get('azp')}"
        if claims["exp"] <= time.time():
            logger.warning("Client search refused: expired %s", who)
            return _refused(HTTPStatus.UNAUTHORIZED, EXPIRED)

    try:
        body = json.loads(request.body)
    except ValueError:
        body = None
    client_ids = body.get("clientIds") if isinstance(body, dict) else None
    if (
        not isinstance(client_ids, list)
        or not client_ids
        or not all(isinstance(client_id, str) for client_id in client_ids)
    ):
        return _refused(HTTPStatus.BAD_REQUEST, NO_CLIENT_IDS)

    rows = _rows(client_ids)
    logger.info(
        "Client search by %s: %d IDs asked, %d found",
        who,
        len(set(client_ids)),
        len(rows),
    )
    return JsonResponse(rows, safe=False)
