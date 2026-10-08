from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routes.pricing import router
from app.services.family_credit_ledger import FamilyCreditLedger
from app.services.pricing_catalog import pricing_catalog


def test_approved_terms_and_no_unverified_purchase_offer():
    catalog = pricing_catalog()
    free, plus, family = catalog["plans"]
    assert (free["avatar_limit"], free["memory_level_limit"], free["weekly_chat_messages"]) == (1, 5, 10)
    assert plus["monthly_credits"] == 50
    assert family["monthly_credits"] * FamilyCreditLedger.UNITS_PER_CREDIT == FamilyCreditLedger.MONTHLY_UNITS
    assert catalog["credits_roll_over"] is True
    assert catalog["purchases_available"] is False
    assert catalog["video_available"] is False
    assert plus["avatar_limit"] == 1


def test_catalog_is_read_only_and_not_cached():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/v1/pricing")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert client.post("/v1/pricing", json={"plan": "family"}).status_code == 405


def test_configured_products_do_not_enable_unfinished_billing(monkeypatch):
    monkeypatch.setenv("STAY_APPLE_PRODUCTS", '{"test.plus":{"plan":"plus","cadence":"monthly"}}')
    catalog = pricing_catalog()
    assert catalog["store_products"] == [{"id": "test.plus", "plan": "plus", "cadence": "monthly"}]
    assert catalog["purchases_available"] is False


def test_invalid_product_configuration_is_not_offered(monkeypatch):
    monkeypatch.setenv("STAY_APPLE_PRODUCTS", '{"test.plus":{"plan":"unlimited","cadence":"monthly"}}')
    assert pricing_catalog()["store_products"] == []
