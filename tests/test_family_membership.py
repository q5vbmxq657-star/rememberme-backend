from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest
from pydantic import ValidationError

from app.services.family_repository import FamilyRepository
from app.routes.family import FamilyCreate, FamilyJoin, FamilyContent, FamilyContentEdit, FamilyCollaborationUpdate


@pytest.fixture
def family_data():
    url = os.getenv("STAY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires isolated PostgreSQL")
    config = conninfo_to_dict(url)
    assert config.get("host", "").startswith("/private/tmp/STAY-AP1-PG-")
    assert config.get("dbname") == "stay_ap1"
    people = [SimpleNamespace(user=SimpleNamespace(user_id=uuid4()), session_id=uuid4()) for _ in range(8)]
    with psycopg.connect(url) as db:
        for person in people:
            db.execute("INSERT INTO users(user_id) VALUES (%s)", (person.user.user_id,))
            db.execute("""INSERT INTO user_sessions(session_id,user_id,refresh_token_hash,access_expires_at,refresh_expires_at)
                VALUES (%s,%s,%s,NOW()+INTERVAL '1 hour',NOW()+INTERVAL '1 day')""",
                       (person.session_id,person.user.user_id,str(uuid4())))
    try:
        yield FamilyRepository(url), people, url
    finally:
        with psycopg.connect(url) as db:
            db.execute("DELETE FROM users WHERE user_id=ANY(%s)", ([p.user.user_id for p in people],))


def test_family_request_requires_approval_and_never_grants_profile_access(family_data):
    repo, people, url = family_data
    owner, guest, stranger = people[:3]
    repo.create(owner, "Our family", "Organizer")
    invitation = repo.invite(owner)
    repo.claim(guest, invitation["code"], "Guest")
    assert repo.snapshot(guest) == {"family": None, "pending_family_name": "Our family"}
    with pytest.raises(HTTPException) as error:
        repo.resolve_invitation(stranger, invitation["invitation_id"], True)
    assert error.value.status_code == 404
    repo.resolve_invitation(owner, invitation["invitation_id"], True)
    assert len(repo.snapshot(guest)["family"]["members"]) == 2
    assert repo.snapshot(guest)["family"]["invitations"] == []
    with psycopg.connect(url) as db:
        assert db.execute("SELECT count(*) FROM profile_memberships WHERE user_id=%s", (guest.user.user_id,)).fetchone()[0] == 0
    with pytest.raises(HTTPException):
        repo.claim(stranger, invitation["code"], "Forwarded")
    with pytest.raises(HTTPException):
        repo.invite(guest)
    repo.remove(guest, guest.user.user_id)
    assert repo.snapshot(guest)["family"] is None
    assert len(repo.snapshot(owner)["family"]["members"]) == 1


def test_revoke_expiry_and_account_deletion_remove_invitations(family_data):
    repo, people, url = family_data
    owner, guest = people[:2]
    repo.create(owner, "Family", "Owner")
    revoked = repo.invite(owner)
    repo.resolve_invitation(owner, revoked["invitation_id"], False)
    with pytest.raises(HTTPException):
        repo.claim(guest, revoked["code"], "Guest")
    expired = repo.invite(owner)
    with psycopg.connect(url) as db:
        db.execute("UPDATE family_invitations SET expires_at=NOW()-INTERVAL '1 second' WHERE invitation_id=%s", (expired["invitation_id"],))
    with pytest.raises(HTTPException):
        repo.claim(guest, expired["code"], "Guest")
    assert repo.snapshot(owner)["family"]["invitations"] == []
    invitation = repo.invite(owner)
    repo.claim(guest, invitation["code"], "Guest")
    with psycopg.connect(url) as db:
        db.execute("DELETE FROM users WHERE user_id=%s", (owner.user.user_id,))
    assert repo.snapshot(guest) == {"family": None, "pending_family_name": None}


def test_concurrent_invitations_never_exceed_six_places(family_data):
    repo, people, _ = family_data
    owner = people[0]
    repo.create(owner, "Family", "Owner")
    def create(_):
        try:
            return repo.invite(owner)
        except HTTPException as error:
            assert error.status_code == 409
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(8)))
    assert sum(result is not None for result in results) == 5
    assert len(repo.snapshot(owner)["family"]["invitations"]) == 5


def test_only_one_claimant_and_single_family_per_user(family_data):
    repo, people, _ = family_data
    owner, second_owner, guest, other = people[:4]
    repo.create(owner, "One", "Owner")
    repo.create(second_owner, "Two", "Owner")
    invitation = repo.invite(owner)
    repo.claim(guest, invitation["code"], "Guest")
    repo.claim(guest, invitation["code"], "Guest")
    with pytest.raises(HTTPException):
        repo.claim(other, invitation["code"], "Other")
    second = repo.invite(second_owner)
    with pytest.raises(HTTPException):
        repo.claim(guest, second["code"], "Guest")
    repo.cancel_request(guest)
    repo.claim(guest, second["code"], "Guest")
    repo.resolve_invitation(second_owner, second["invitation_id"], True)
    with pytest.raises(HTTPException):
        repo.create(guest, "Three", "Guest")


def test_revoked_session_cannot_read_or_modify_family(family_data):
    repo, people, url = family_data
    owner = people[0]
    repo.create(owner, "Family", "Owner")
    with psycopg.connect(url) as db:
        db.execute("UPDATE user_sessions SET revoked_at=NOW() WHERE session_id=%s", (owner.session_id,))
    for action in [repo.snapshot, repo.invite, repo.close]:
        with pytest.raises(HTTPException) as error:
            action(owner)
        assert error.value.status_code == 401


def test_validation_rejects_empty_names_and_invalid_codes():
    with pytest.raises(ValidationError):
        FamilyCreate(name="   ", display_name="Person")
    with pytest.raises(ValidationError):
        FamilyJoin(code="short", display_name="Person")
    assert FamilyCreate(name=" Family ", display_name=" Person ").name == "Family"


def add_member(repo, owner, person):
    invitation = repo.invite(owner)
    repo.claim(person, invitation["code"], "Member")
    repo.resolve_invitation(owner, invitation["invitation_id"], True)


@pytest.fixture
def content_data(family_data):
    repo, people, url = family_data
    profile = uuid4()
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO digital_human_profiles(profile_id) VALUES (%s)", (profile,))
        db.execute("INSERT INTO profile_memberships(membership_id,user_id,profile_id,role) VALUES (%s,%s,%s,'owner')",
                   (uuid4(),people[0].user.user_id,profile))
    repo.create(people[0], "Family", "Owner")
    add_member(repo, people[0], people[1])
    try:
        yield repo, people, url, profile
    finally:
        with psycopg.connect(url) as db:
            db.execute("DELETE FROM digital_human_profiles WHERE profile_id=%s", (profile,))


def story(family_id, revision=0, text="A shared afternoon"):
    return FamilyContent(family_id=family_id, profile_name="Person", title="An afternoon", text=text, kind="story", expected_revision=revision)


def test_excluded_story_can_be_shared_without_provider_index_or_consent(content_data):
    repo, people, url, profile = content_data
    memory = uuid4()
    fid = repo.snapshot(people[0])["family"]["family_id"]
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO memory_usage_decisions(profile_id,memory_id,included,revision) VALUES (%s,%s,FALSE,1)",
                   (profile,str(memory)))
    assert repo.publish_content(people[0], profile, memory, story(fid)) == {"revision": 1}
    visible = repo.library(people[1])["items"]
    assert len(visible) == 1 and visible[0]["payload"]["text"] == "A shared afternoon"
    with psycopg.connect(url) as db:
        assert db.execute("SELECT count(*) FROM memory_embeddings WHERE profile_id=%s", (str(profile),)).fetchone()[0] == 0
        assert not db.execute("SELECT included FROM memory_usage_decisions WHERE profile_id=%s AND memory_id=%s",
                              (profile,str(memory))).fetchone()[0]
        assert db.execute("SELECT count(*) FROM profile_memberships WHERE user_id=%s", (people[1].user.user_id,)).fetchone()[0] == 0


def test_family_publication_conflicts_retry_withdrawal_and_departure(content_data):
    repo, people, _, profile = content_data
    owner, member, stranger = people[:3]
    memory = uuid4()
    fid = repo.snapshot(owner)["family"]["family_id"]
    repo.publish_content(owner, profile, memory, story(fid))
    assert repo.publish_content(owner, profile, memory, story(fid)) == {"revision": 1}
    assert repo.content_item(member, profile, memory, 1)["payload"]["text"] == "A shared afternoon"
    for actor in [member, stranger]:
        with pytest.raises(HTTPException):
            repo.publish_content(actor, profile, memory, story(fid, 1))
        with pytest.raises(HTTPException):
            repo.withdraw_content(actor, profile, memory, 1)
    assert repo.publish_content(owner, profile, memory, story(fid, 1, "Updated")) == {"revision": 2}
    with pytest.raises(HTTPException) as error:
        repo.publish_content(owner, profile, memory, story(fid, 1, "Stale"))
    assert error.value.status_code == 409
    repo.withdraw_content(owner, profile, memory, 2)
    with pytest.raises(HTTPException):
        repo.content_item(member, profile, memory, 2)
    assert repo.withdraw_content(owner, profile, memory, 2) == {"revision": 3}
    assert repo.library(member)["items"] == []
    assert repo.library(owner)["items"][0]["payload"] is None
    with pytest.raises(HTTPException):
        repo.publish_content(owner, profile, memory, story(fid))
    repo.publish_content(owner, profile, memory, story(fid, 3))
    repo.propose_handover(owner, member.user.user_id)
    repo.resolve_handover(member, repo.snapshot(member)["family"]["handover"]["transfer_id"], True)
    repo.remove(owner, owner.user.user_id)
    assert repo.library(member)["items"] == []


def test_original_deletion_withdraws_family_publication_and_blocks_recreation(content_data):
    repo, people, url, profile = content_data
    memory = uuid4()
    fid = repo.snapshot(people[0])["family"]["family_id"]
    repo.publish_content(people[0], profile, memory, story(fid))
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO memory_deletion_tombstones(profile_id,memory_id) VALUES (%s,%s)", (profile,str(memory)))
    assert repo.library(people[1])["items"] == []
    with pytest.raises(HTTPException) as error:
        repo.publish_content(people[0], profile, memory, story(fid, 2))
    assert error.value.status_code == 409


def test_family_content_rejects_implicit_training_fields_and_invalid_media():
    with pytest.raises(ValidationError):
        FamilyContent(**{**story(uuid4()).model_dump(), "confirmed_address": "Honey"})
    with pytest.raises(ValidationError):
        FamilyContent(profile_name="Person", title="Photo", kind="photo", expected_revision=0)


def test_family_content_is_pinned_to_the_reviewed_recipients(content_data):
    repo, people, _, profile = content_data
    with pytest.raises(HTTPException) as error:
        repo.publish_content(people[0], profile, uuid4(), story(uuid4()))
    assert error.value.status_code == 409
    assert repo.library(people[1])["items"] == []


def test_concurrent_family_edits_do_not_overwrite_each_other(content_data):
    repo, people, _, profile = content_data
    owner = people[0]
    fid = repo.snapshot(owner)["family"]["family_id"]
    memory = uuid4()
    repo.publish_content(owner, profile, memory, story(fid))
    def update(text):
        try:
            return repo.publish_content(owner, profile, memory, story(fid, 1, text))["revision"]
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, ["One", "Two"])) == [2, 409]


def test_family_library_paginates_without_silently_dropping_items(content_data):
    repo, people, _, profile = content_data
    fid = repo.snapshot(people[0])["family"]["family_id"]
    for _ in range(51):
        repo.publish_content(people[0], profile, uuid4(), story(fid))
    first = repo.library(people[1])
    second = repo.library(people[1], first["next_profile_id"], first["next_memory_id"])
    assert len(first["items"]) == 50 and len(second["items"]) == 1
    assert second["next_memory_id"] is None
    assert len({item["memory_id"] for item in first["items"] + second["items"]}) == 51


@pytest.mark.parametrize("action", ["close", "revoke"])
def test_closure_or_profile_revocation_erases_shared_payload(content_data, action):
    repo, people, url, profile = content_data
    fid = repo.snapshot(people[0])["family"]["family_id"]
    memory = uuid4()
    repo.publish_content(people[0], profile, memory, story(fid))
    if action == "close":
        repo.close(people[0])
    else:
        with psycopg.connect(url) as db:
            db.execute("UPDATE profile_memberships SET status='revoked' WHERE profile_id=%s", (profile,))
        assert repo.library(people[1])["items"] == []
    with psycopg.connect(url) as db:
        assert db.execute("SELECT payload,family_id FROM family_content WHERE profile_id=%s AND memory_id=%s",
                          (profile,memory)).fetchone() == (None,None)


def test_family_media_reuses_gallery_storage_and_checks_each_download(content_data, monkeypatch, tmp_path):
    from app.services.avatar_media_storage_service import AvatarMediaStorageService
    repo, people, _, profile = content_data
    fid = repo.snapshot(people[0])["family"]["family_id"]
    memory, asset = uuid4(), uuid4()
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"fixture")
    metadata = SimpleNamespace(profile_id=str(profile), asset_type="memory_image", storage_path=str(path))
    monkeypatch.setattr(AvatarMediaStorageService, "get_metadata", lambda self, _: metadata)
    body = FamilyContent(family_id=fid, profile_name="Person", title="Photo", kind="photo", asset_id=asset, expected_revision=0)
    metadata.asset_type = "image"
    with pytest.raises(HTTPException):
        repo.publish_content(people[0], profile, memory, body)
    metadata.asset_type = "memory_image"
    repo.publish_content(people[0], profile, memory, body)
    assert repo.content_media(people[1], profile, memory, 1) is metadata
    with pytest.raises(HTTPException):
        repo.content_media(people[1], profile, memory, 2)
    with pytest.raises(HTTPException):
        repo.content_media(people[2], profile, memory, 1)
    repo.withdraw_content(people[0], profile, memory, 1)
    with pytest.raises(HTTPException):
        repo.content_media(people[1], profile, memory, 1)


def test_family_editing_requires_author_opt_in_and_never_grants_profile_access(content_data):
    repo, people, url, profile = content_data
    owner, member, stranger = people[:3]
    fid = repo.snapshot(owner)["family"]["family_id"]
    memory = uuid4()
    repo.publish_content(owner, profile, memory, story(fid))
    draft = FamilyContentEdit(family_id=fid, expected_revision=1, title="Our story", text="Together")
    with pytest.raises(HTTPException) as error:
        repo.edit_content(member, profile, memory, draft)
    assert error.value.status_code == 403
    permission = FamilyCollaborationUpdate(family_id=fid, expected_revision=1, allows_family_edits=True)
    with pytest.raises(HTTPException):
        repo.update_collaboration(member, profile, memory, permission)
    updated = repo.update_collaboration(owner, profile, memory, permission)
    assert updated["revision"] == 2 and updated["allows_family_edits"]
    draft = draft.model_copy(update={"expected_revision": 2})
    with pytest.raises(HTTPException):
        repo.edit_content(stranger, profile, memory, draft)
    updated = repo.edit_content(member, profile, memory, draft)
    assert updated["revision"] == 3 and updated["payload"]["text"] == "Together"
    assert updated["last_editor_id"] == member.user.user_id
    assert updated["author_id"] == owner.user.user_id
    assert updated["payload"]["profile_name"] == "Person"
    with pytest.raises(HTTPException) as error:
        repo.edit_content(owner, profile, memory, draft.model_copy(update={"text": "Stale"}))
    assert error.value.status_code == 409
    with psycopg.connect(url) as db:
        assert db.execute("SELECT count(*) FROM profile_memberships WHERE user_id=%s", (member.user.user_id,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM memory_embeddings WHERE profile_id=%s", (str(profile),)).fetchone()[0] == 0


def test_family_edit_permission_revocation_and_republication_reset(content_data):
    repo, people, _, profile = content_data
    owner, member = people[:2]
    fid = repo.snapshot(owner)["family"]["family_id"]
    memory = uuid4()
    repo.publish_content(owner, profile, memory, story(fid))
    repo.update_collaboration(owner, profile, memory,
        FamilyCollaborationUpdate(family_id=fid, expected_revision=1, allows_family_edits=True))
    repo.update_collaboration(owner, profile, memory,
        FamilyCollaborationUpdate(family_id=fid, expected_revision=2, allows_family_edits=False))
    with pytest.raises(HTTPException):
        repo.edit_content(member, profile, memory,
            FamilyContentEdit(family_id=fid, expected_revision=3, title="No", text="No"))
    repo.update_collaboration(owner, profile, memory,
        FamilyCollaborationUpdate(family_id=fid, expected_revision=3, allows_family_edits=True))
    repo.withdraw_content(owner, profile, memory, 4)
    repo.publish_content(owner, profile, memory, story(fid, 5))
    assert not repo.content_item(owner, profile, memory, None)["allows_family_edits"]


def test_family_collaboration_does_not_accept_identity_or_media_replacement():
    with pytest.raises(ValidationError):
        FamilyContentEdit(family_id=uuid4(), expected_revision=1, title="Story", text="Text", author_id=uuid4())
    with pytest.raises(ValidationError):
        FamilyContentEdit(family_id=uuid4(), expected_revision=1, title="Story", text="Text", asset_id=uuid4())


def test_handover_requires_current_nominee_acceptance_and_preserves_family(family_data):
    repo, people, url = family_data
    owner, nominee, other = people[:3]
    repo.create(owner, "Family", "Owner")
    add_member(repo, owner, nominee)
    add_member(repo, owner, other)
    repo.propose_handover(owner, nominee.user.user_id)
    snapshot = repo.snapshot(owner)["family"]
    transfer = snapshot["handover"]["transfer_id"]
    assert snapshot["organizer_id"] == owner.user.user_id
    for outsider in [owner, other]:
        with pytest.raises(HTTPException):
            repo.resolve_handover(outsider, transfer, True)
    repo.resolve_handover(nominee, transfer, True)
    assert repo.snapshot(nominee)["family"]["organizer_id"] == nominee.user.user_id
    assert repo.snapshot(nominee)["family"]["handover"] is None
    with pytest.raises(HTTPException):
        repo.invite(owner)
    repo.remove(owner, owner.user.user_id)
    with psycopg.connect(url) as db:
        db.execute("DELETE FROM users WHERE user_id=%s", (owner.user.user_id,))
    assert len(repo.snapshot(nominee)["family"]["members"]) == 2


def test_stale_removed_or_expired_nominee_cannot_accept(family_data):
    repo, people, url = family_data
    owner, first, second = people[:3]
    repo.create(owner, "Family", "Owner")
    add_member(repo, owner, first)
    add_member(repo, owner, second)
    repo.propose_handover(owner, first.user.user_id)
    stale = repo.snapshot(owner)["family"]["handover"]["transfer_id"]
    repo.propose_handover(owner, second.user.user_id)
    with pytest.raises(HTTPException):
        repo.resolve_handover(first, stale, True)
    repo.remove(second, second.user.user_id)
    assert repo.snapshot(owner)["family"]["handover"] is None
    repo.propose_handover(owner, first.user.user_id)
    expired = repo.snapshot(owner)["family"]["handover"]["transfer_id"]
    with psycopg.connect(url) as db:
        db.execute("UPDATE family_organizer_transfers SET expires_at=NOW()-INTERVAL '1 second' WHERE transfer_id=%s", (expired,))
    with pytest.raises(HTTPException):
        repo.resolve_handover(first, expired, True)
    repo.purge_expired()
    assert repo.snapshot(owner)["family"]["handover"] is None


def test_names_are_scoped_to_current_member_and_organizer(family_data):
    repo, people, _ = family_data
    owner, member = people[:2]
    repo.create(owner, "Old family", "Owner")
    add_member(repo, owner, member)
    with pytest.raises(HTTPException):
        repo.rename_family(member, "Not authorized")
    repo.rename_family(owner, "New family")
    repo.rename_member(member, "My name")
    snapshot = repo.snapshot(owner)["family"]
    assert snapshot["name"] == "New family"
    assert {m["user_id"]: m["display_name"] for m in snapshot["members"]} == {
        owner.user.user_id: "Owner", member.user.user_id: "My name"}


def test_member_cannot_remove_organizer_or_another_member(family_data):
    repo, people, _ = family_data
    owner, first, second = people[:3]
    repo.create(owner, "Family", "Owner")
    for person in [first, second]:
        invitation = repo.invite(owner)
        repo.claim(person, invitation["code"], "Member")
        repo.resolve_invitation(owner, invitation["invitation_id"], True)
    for target in [owner, second]:
        with pytest.raises(HTTPException):
            repo.remove(first, target.user.user_id)
    with pytest.raises(HTTPException):
        repo.close(first)
    assert len(repo.snapshot(owner)["family"]["members"]) == 3
    repo.close(owner)
    assert repo.snapshot(first)["family"] is None


def test_invitation_secret_is_not_persisted_or_returned_in_snapshot(family_data):
    repo, people, url = family_data
    owner = people[0]
    repo.create(owner, "Family", "Owner")
    invitation = repo.invite(owner)
    assert invitation["code"] not in str(repo.snapshot(owner))
    with psycopg.connect(url) as db:
        stored = db.execute("SELECT token_digest FROM family_invitations WHERE invitation_id=%s", (invitation["invitation_id"],)).fetchone()[0]
    assert stored != invitation["code"]
    assert len(stored) == 64


def test_expired_requests_are_purged_without_user_returning(family_data):
    repo, people, url = family_data
    owner, guest = people[:2]
    repo.create(owner, "Family", "Owner")
    expired = repo.invite(owner)
    valid = repo.invite(owner)
    repo.claim(guest, expired["code"], "Guest")
    with psycopg.connect(url) as db:
        db.execute("UPDATE family_invitations SET expires_at=NOW()-INTERVAL '1 second' WHERE invitation_id=%s", (expired["invitation_id"],))
    repo.purge_expired()
    with psycopg.connect(url) as db:
        assert db.execute("SELECT 1 FROM family_invitations WHERE invitation_id=%s", (expired["invitation_id"],)).fetchone() is None
        assert db.execute("SELECT 1 FROM family_invitations WHERE invitation_id=%s", (valid["invitation_id"],)).fetchone() is not None
