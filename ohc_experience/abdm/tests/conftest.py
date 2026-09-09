import pytest


@pytest.fixture(autouse=True)
def offline_lgd(lgd_lookup):
    """ABDM tests use the shared deterministic LGD fixture."""
    return lgd_lookup
