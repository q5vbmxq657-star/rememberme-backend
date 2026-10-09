import hashlib

import pytest

from app.services.apple_store_configuration import configured_products, configured_roots


def test_default_products_match_approved_store_identifiers(monkeypatch):
    monkeypatch.delenv("STAY_APPLE_PRODUCTS", raising=False)
    products = configured_products()
    assert set(products) == {"stay.plus.monthly", "stay.plus.annual", "stay.family.monthly", "stay.family.annual"}
    for identity, product in products.items():
        assert identity == f"stay.{product.plan}.{product.cadence}"


@pytest.mark.parametrize("value", ["{}", "[]", "null", "invalid", '{" ":{}}'])
def test_invalid_override_never_silently_uses_defaults(monkeypatch, value):
    monkeypatch.setenv("STAY_APPLE_PRODUCTS", value)
    with pytest.raises((ValueError, TypeError)):
        configured_products()


def test_bundled_root_is_apple_g3(monkeypatch):
    monkeypatch.delenv("STAY_APPLE_ROOT_CERTIFICATES", raising=False)
    roots = configured_roots()
    assert len(roots) == 1
    assert hashlib.sha256(roots[0]).hexdigest() == "63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179"


def test_sandbox_verifier_can_load_bundled_configuration(monkeypatch):
    from app.routes.billing import configured_verifier
    from appstoreserverlibrary.models.Environment import Environment

    monkeypatch.delenv("STAY_APPLE_PRODUCTS", raising=False)
    monkeypatch.delenv("STAY_APPLE_ROOT_CERTIFICATES", raising=False)
    monkeypatch.setenv("STAY_APPLE_BUNDLE_ID", "com.obernburg2025.RemembermeAI")
    monkeypatch.setenv("STAY_APPLE_APP_ID", "6780634832")
    monkeypatch.setenv("STAY_APPLE_ENVIRONMENT", "Sandbox")
    verifier = configured_verifier()
    assert verifier.environment == Environment.SANDBOX
    assert verifier.bundle_id == "com.obernburg2025.RemembermeAI"


@pytest.mark.parametrize("value", ["[]", "null", '"path"', "[null]"])
def test_empty_or_invalid_trust_override_fails_closed(monkeypatch, value):
    monkeypatch.setenv("STAY_APPLE_ROOT_CERTIFICATES", value)
    with pytest.raises(ValueError):
        configured_roots()
