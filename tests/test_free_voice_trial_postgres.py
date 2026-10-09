"""Isolated schema: real ledger migrations and concurrent trial redemption."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from app.services.free_voice_trial import grant_free_voice_trial
from app.services.family_credit_ledger import FamilyCreditLedger as Ledger, PersonalCreditAccount
from app.services.apple_purchase_registry import ApplePurchaseRegistry


def test_parallel_redemption_and_exhaustion():
    url = os.environ.get("STAY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("An isolated test database is required")
    from psycopg.conninfo import conninfo_to_dict
    assert conninfo_to_dict(url).get("host", "").startswith("/private/tmp/stay-quota-db.")
    schema = "trial_" + uuid4().hex
    uid = uuid4()
    with psycopg.connect(url) as db:
        db.execute(f'CREATE SCHEMA "{schema}"')
        db.execute(f'SET search_path TO "{schema}"')
        db.execute("CREATE TABLE users(user_id UUID PRIMARY KEY)")
        db.execute("CREATE TABLE family_groups(family_id UUID PRIMARY KEY)")
        db.execute("CREATE TABLE family_members(family_id UUID,user_id UUID)")
        db.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
        root = Path(__file__).resolve().parents[1] / "migrations"
        for name in ("037_family_credit_ledger", "039_apple_purchase_registry",
                     "041_family_subscription_fulfillment", "043_personal_credit_accounts",
                     "046_personal_call_reservations"):
            db.execute((root / f"{name}.sql").read_text())
        db.execute("INSERT INTO users VALUES (%s)", (uid,))
    def redeem(_):
        with psycopg.connect(url, row_factory=dict_row) as db:
            db.execute(f'SET search_path TO "{schema}"')
            grant_free_voice_trial(db, uid)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(redeem, range(16)))
        with psycopg.connect(url, row_factory=dict_row) as db:
            db.execute(f'SET search_path TO "{schema}"')
            owner = PersonalCreditAccount(ApplePurchaseRegistry.account_token(db, uid))
            assert Ledger.balance(db, owner)["available_units"] == 300
            call = uuid4()
            Ledger.reserve(db, owner, uid, call, mode="voice", seconds=300)
            assert Ledger.balance(db, owner)["available_units"] == 0
            Ledger.settle(db, owner, call, verified_seconds=300)
        redeem(None)
        with psycopg.connect(url, row_factory=dict_row) as db:
            db.execute(f'SET search_path TO "{schema}"')
            assert Ledger.balance(db, owner)["available_units"] == 0
            assert db.execute("SELECT count(*) AS n FROM family_credit_entries WHERE kind='grant'").fetchone()["n"] == 1
    finally:
        with psycopg.connect(url) as db:
            db.execute(f'DROP SCHEMA "{schema}" CASCADE')
