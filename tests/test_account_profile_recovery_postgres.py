"""Profile recovery SQL against a disposable local database; billing resolution is fixed to Free."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest
from fastapi import HTTPException

from app.services import profile_membership_repository as module


@pytest.fixture
def database(monkeypatch):
    url = os.environ.get("STAY_RECOVERY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Disposable STAY_RECOVERY_TEST_DATABASE_URL required")
    assert conninfo_to_dict(url).get("host", "").startswith("/private/tmp/stay-recovery-db.")
    schema = "recovery_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as db:
        db.execute(f'CREATE SCHEMA "{schema}"')
    scoped = url + f" options='-c search_path={schema}'"
    with psycopg.connect(scoped, autocommit=True) as db:
        root = Path(__file__).resolve().parents[1] / "migrations"
        for name in ("002_digital_human_profiles.sql", "011_user_identity_profile_memberships.sql", "012_user_sessions.sql"):
            db.execute((root / name).read_text())
        db.execute("CREATE TABLE digital_human_profile_erasure_requests(profile_id uuid PRIMARY KEY)")
    monkeypatch.setattr(module, "effective_plan", lambda cursor, user_id: "free")
    uid, sid = uuid4(), uuid4()
    with psycopg.connect(scoped) as db:
        db.execute("INSERT INTO users(user_id) VALUES (%s)", (uid,))
        db.execute("""INSERT INTO user_sessions(session_id,user_id,refresh_token_hash,access_expires_at,refresh_expires_at)
            VALUES (%s,%s,%s,NOW()+INTERVAL '1 hour',NOW()+INTERVAL '1 day')""", (sid, uid, str(uuid4())))
    yield module.ProfileMembershipRepository(scoped), scoped, uid, sid
    with psycopg.connect(url, autocommit=True) as db:
        db.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_reinstall_replays_existing_id_above_limit_without_creating_an_avatar(database):
    repository, url, uid, sid = database
    pid = uuid4()
    repository.provision_owned_profile(user_id=uid, profile_id=pid, consent_verified=True,
                                       display_name="Anna", relationship="Mother")
    with pytest.raises(HTTPException) as error:
        repository.provision_owned_profile(user_id=uid, profile_id=uuid4(), consent_verified=True)
    assert error.value.status_code == 402
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: repository.provision_owned_profile(
            user_id=uid, profile_id=pid, consent_verified=False), range(8)))
    assert all(m.profile_id == pid and not created for m, created in results)
    directory = repository.account_directory(user_id=uid, session_id=sid)
    assert len(directory) == 1
    assert directory[0]["display_name"] == "Anna"
    with psycopg.connect(url) as db:
        assert db.execute("SELECT COUNT(*) FROM digital_human_profiles").fetchone()[0] == 1


def test_directory_rejects_other_sessions_revocation_and_erasure(database):
    repository, url, uid, sid = database
    pid = uuid4()
    repository.provision_owned_profile(user_id=uid, profile_id=pid, consent_verified=True)
    assert len(repository.account_directory(user_id=uid, session_id=sid)) == 1
    assert repository.account_directory(user_id=uuid4(), session_id=sid) == []
    assert repository.account_directory(user_id=uid, session_id=uuid4()) == []
    with psycopg.connect(url) as db:
        db.execute("UPDATE user_sessions SET revoked_at=NOW() WHERE session_id=%s", (sid,))
    assert repository.account_directory(user_id=uid, session_id=sid) == []
    with psycopg.connect(url) as db:
        db.execute("UPDATE user_sessions SET revoked_at=NULL WHERE session_id=%s", (sid,))
        db.execute("INSERT INTO digital_human_profile_erasure_requests VALUES (%s)", (pid,))
    assert repository.account_directory(user_id=uid, session_id=sid) == []


def test_placeholder_is_not_a_recovered_identity_and_known_name_is_preserved(database):
    repository, url, uid, sid = database
    pid = uuid4()
    repository.provision_owned_profile(user_id=uid, profile_id=pid, consent_verified=True)
    with psycopg.connect(url) as db:
        db.execute("UPDATE digital_human_profiles SET metadata=%s::jsonb WHERE profile_id=%s",
                   ('{"display_name":"Recovered memory space"}', pid))
    assert repository.account_directory(user_id=uid, session_id=sid)[0]["display_name"] is None
    repository.provision_owned_profile(user_id=uid, profile_id=pid, consent_verified=False,
                                       display_name="Anna", relationship="Mother")
    repository.provision_owned_profile(user_id=uid, profile_id=pid, consent_verified=False,
                                       display_name="Wrong name", relationship="Other")
    assert repository.account_directory(user_id=uid, session_id=sid)[0]["display_name"] == "Anna"
