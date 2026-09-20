"""Starting the external chains, for the code that decides they should run.

Both defer to after the caller's transaction commits, so no adapter is ever
asked to create a client for a registration that then rolled back.

There is no separate retry pair. A retry is the same call — the chain skips
every system whose ledger row is already ACTIVE, so re-running it finishes the
missing ones.

Reading and rotating the secret live in `credentials.py`, not here: this module
imports `tasks`, which pulls in the adapter packages that a view may not reach.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ohc_experience.integrations.tasks import CHAIN
from ohc_experience.integrations.tasks import enqueue_bridge_sync
from ohc_experience.integrations.tasks import enqueue_chain
from ohc_experience.integrations.tasks import enqueue_teardown

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

    from ohc_experience.experiences.models import Product


def start_provisioning(
    product: Product,
    started_by: AbstractBaseUser | None = None,
) -> None:
    enqueue_chain(product, started_by=started_by)


def start_bridge_sync(product: Product) -> None:
    """Register the saved callback URL with HIE-CM, or update what is there."""
    enqueue_bridge_sync(product)


def start_deprovisioning(product: Product) -> None:
    """Revocation, and the retry of one.

    No attempt record: what teardown reports is the ledger emptying, which the
    ledger already says.
    """
    enqueue_teardown(product)


def provision_inline(product: Product) -> None:
    """Run the chain here and now, for a caller that cannot wait for a worker.

    Ledger-guarded like every other route through it, so a scheduled run that
    also lands finds the work already done.
    """
    for task in CHAIN:
        task(product.pk)
