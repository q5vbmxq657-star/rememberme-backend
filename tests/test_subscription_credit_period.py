from datetime import datetime, timezone
import pytest

from app.services.subscription_credit_period import monthly_credit_periods


def utc(year, month, day):
    return datetime(year, month, day, tzinfo=timezone.utc)


def periods(**changes):
    values = dict(original_transaction_id="123456789", environment="Production",
                  paid_from=utc(2028, 1, 31), paid_until=utc(2029, 1, 31),
                  now=utc(2028, 1, 31), cadence="annual")
    values.update(changes)
    return monthly_credit_periods(**values)


def test_annual_payment_does_not_grant_twelve_months_in_advance():
    assert len(periods()) == 1
    assert len(periods(now=utc(2028, 1, 30))) == 0


def test_month_end_anchor_does_not_drift_after_february():
    result = periods(now=utc(2028, 3, 31))
    assert [p.starts_at for p in result] == [utc(2028, 1, 31), utc(2028, 2, 29), utc(2028, 3, 31)]
    assert result[1].ends_at == result[2].starts_at


def test_catchup_is_bounded_and_keys_are_stable():
    assert len(periods(now=utc(2030, 1, 1))) == 12
    assert periods()[0].evidence_key == periods(now=utc(2028, 3, 31))[0].evidence_key
    assert periods()[0].evidence_key != periods(environment="Sandbox")[0].evidence_key
    assert periods()[0].evidence_key != periods(original_transaction_id="987")[0].evidence_key


def test_monthly_receipt_grants_one_allowance_only():
    assert len(periods(cadence="monthly", paid_until=utc(2028, 2, 29), now=utc(2030, 1, 1))) == 1


@pytest.mark.parametrize("change", [
    {"paid_from": datetime(2028, 1, 31)}, {"now": datetime(2028, 2, 1)},
    {"paid_until": utc(2028, 1, 31)}, {"paid_until": utc(2030, 1, 1)},
    {"environment": "unknown"}, {"original_transaction_id": "client-issued"},
    {"original_transaction_id": ""}, {"cadence": "weekly"},
])
def test_unusable_verified_period_data_is_rejected(change):
    with pytest.raises(ValueError):
        periods(**change)
