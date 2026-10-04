"""Monthly allowance scheduling after server-side purchase verification.

This module does not verify Apple transactions and exposes no HTTP grant route.
The billing verifier must supply the original subscription identity and the
verified paid interval. A yearly payment grants only elapsed monthly allowances.
"""
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256

from app.services.family_credit_ledger import FamilyCreditLedger


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Billing timestamps must include a timezone.")
    return value.astimezone(timezone.utc)


def _anniversary(start: datetime, months: int) -> datetime:
    year, month = divmod(start.year * 12 + start.month - 1 + months, 12)
    month += 1
    return start.replace(year=year, month=month,
                         day=min(start.day, monthrange(year, month)[1]))


@dataclass(frozen=True)
class SubscriptionCreditPeriod:
    starts_at: datetime
    ends_at: datetime
    evidence_key: str


def monthly_credit_periods(*, original_transaction_id: str, environment: str,
                          paid_from: datetime, paid_until: datetime,
                          now: datetime, cadence: str) -> tuple[SubscriptionCreditPeriod, ...]:
    if (not isinstance(original_transaction_id, str) or not original_transaction_id.isascii()
            or not original_transaction_id.isdecimal() or len(original_transaction_id) > 128):
        raise ValueError("A verified Apple original transaction identity is required.")
    if environment not in {"Production", "Sandbox"} or cadence not in {"monthly", "annual"}:
        raise ValueError("Unsupported billing environment or cadence.")
    start, end, clock = map(_utc, (paid_from, paid_until, now))
    count = 1 if cadence == "monthly" else 12
    if end <= start or end > _anniversary(start, count):
        raise ValueError("The paid interval does not match the subscription cadence.")
    periods = []
    for index in range(count):
        boundary = _anniversary(start, index)
        if boundary >= end or boundary > clock:
            break
        identity = f"apple:{environment}:{original_transaction_id}:{boundary.isoformat()}"
        periods.append(SubscriptionCreditPeriod(
            starts_at=boundary, ends_at=min(_anniversary(start, index + 1), end),
            evidence_key="subscription-period:" + sha256(identity.encode()).hexdigest()))
    return tuple(periods)


def grant_family_periods(db, family_id, periods: tuple[SubscriptionCreditPeriod, ...]):
    """Invoke within the verified subscription owner's existing transaction."""
    for period in periods:
        FamilyCreditLedger.grant(db, family_id, evidence_key=period.evidence_key,
                                units=FamilyCreditLedger.MONTHLY_UNITS)
    return FamilyCreditLedger.balance(db, family_id)
