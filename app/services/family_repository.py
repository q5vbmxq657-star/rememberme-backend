"""Explicit Family membership and publications; never grants broad profile access."""
from contextlib import contextmanager
import hashlib
import os
import secrets
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import HTTPException
from app.services.family_credit_ledger import FamilyCreditLedger


class FamilyRepository:
    def __init__(self, database_url=None):
        self.database_url = (database_url or os.environ.get("DATABASE_URL", "")).strip()
        if not self.database_url:
            raise HTTPException(503, "Family is temporarily unavailable.")

    @contextmanager
    def transaction(self, principal):
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as db:
            db.execute("SET LOCAL lock_timeout = '5s'")
            db.execute("SET LOCAL statement_timeout = '15s'")
            # Revalidate the authenticated session inside the mutation transaction.
            valid = db.execute("""SELECT u.user_id FROM users u JOIN user_sessions s USING (user_id)
                WHERE u.user_id=%s AND u.status='active' AND s.session_id=%s
                AND s.revoked_at IS NULL AND s.access_expires_at>NOW()
                AND s.refresh_expires_at>NOW() FOR SHARE OF u, s""",
                (principal.user.user_id, principal.session_id)).fetchone()
            if not valid:
                raise HTTPException(401, "Please sign in again.")
            # Serialize this account's create/join/leave operations across devices.
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                       ("family-user:" + str(principal.user.user_id),))
            yield db

    @staticmethod
    def group(db, user_id, organizer=False):
        row = db.execute("""SELECT g.* FROM family_groups g JOIN family_members m USING(family_id)
            WHERE m.user_id=%s FOR UPDATE OF g""", (user_id,)).fetchone()
        if not row or (organizer and row["organizer_id"] != user_id):
            raise HTTPException(404, "Family not found.")
        return row

    @staticmethod
    def prune(db, family_id):
        db.execute("DELETE FROM family_invitations WHERE family_id=%s AND expires_at<=NOW()", (family_id,))

    def snapshot(self, principal):
        uid = principal.user.user_id
        with self.transaction(principal) as db:
            group = db.execute("""SELECT g.* FROM family_groups g JOIN family_members m USING(family_id)
                WHERE m.user_id=%s FOR UPDATE OF g""", (uid,)).fetchone()
            if not group:
                pending = db.execute("""SELECT g.name FROM family_invitations i JOIN family_groups g USING(family_id)
                    WHERE i.claimant_id=%s AND i.expires_at>NOW()""", (uid,)).fetchone()
                return {"family": None, "pending_family_name": pending["name"] if pending else None}
            self.prune(db, group["family_id"])
            members = db.execute("""SELECT user_id, display_name FROM family_members
                WHERE family_id=%s ORDER BY joined_at, user_id""", (group["family_id"],)).fetchall()
            invitations = []
            if group["organizer_id"] == uid:
                invitations = db.execute("""SELECT invitation_id, expires_at, claimant_name
                    FROM family_invitations WHERE family_id=%s ORDER BY created_at""", (group["family_id"],)).fetchall()
            handover = db.execute("""SELECT transfer_id,candidate_id,expires_at FROM family_organizer_transfers
                WHERE family_id=%s AND expires_at>NOW()""", (group["family_id"],)).fetchone()
            return {"family": {"family_id": group["family_id"], "name": group["name"],
                    "organizer_id": group["organizer_id"], "current_user_id": uid, "members": members,
                    "invitations": invitations, "handover": handover,
                    "credits": FamilyCreditLedger.balance(db, group["family_id"])}, "pending_family_name": None}

    def credits(self, principal):
        with self.transaction(principal) as db:
            group = self.group(db, principal.user.user_id)
            return {"family_id": group["family_id"], **FamilyCreditLedger.balance(db, group["family_id"])}

    def create(self, principal, name, display_name):
        uid = principal.user.user_id
        with self.transaction(principal) as db:
            if db.execute("SELECT 1 FROM family_members WHERE user_id=%s", (uid,)).fetchone():
                raise HTTPException(409, "You already belong to a family.")
            fid = uuid4()
            db.execute("INSERT INTO family_groups(family_id,name,organizer_id) VALUES (%s,%s,%s)", (fid,name,uid))
            db.execute("INSERT INTO family_members(user_id,family_id,display_name) VALUES (%s,%s,%s)", (uid,fid,display_name))
            db.execute("DELETE FROM family_invitations WHERE claimant_id=%s", (uid,))

    def invite(self, principal):
        with self.transaction(principal) as db:
            group = self.group(db, principal.user.user_id, organizer=True)
            fid = group["family_id"]
            self.prune(db, fid)
            count = db.execute("""SELECT (SELECT count(*) FROM family_members WHERE family_id=%s)
                + (SELECT count(*) FROM family_invitations WHERE family_id=%s) AS seats""", (fid,fid)).fetchone()["seats"]
            if count >= 6:
                raise HTTPException(409, "All six places are in use or reserved. Remove an invitation to free a place.")
            token = secrets.token_urlsafe(32)
            row = db.execute("""INSERT INTO family_invitations(invitation_id,family_id,token_digest,expires_at)
                VALUES (%s,%s,%s,NOW()+INTERVAL '7 days') RETURNING invitation_id,expires_at""",
                (uuid4(),fid,hashlib.sha256(token.encode()).hexdigest())).fetchone()
            return {**row, "code": token}

    def claim(self, principal, code, display_name):
        uid = principal.user.user_id
        digest = hashlib.sha256(code.encode()).hexdigest()
        with self.transaction(principal) as db:
            if db.execute("SELECT 1 FROM family_members WHERE user_id=%s", (uid,)).fetchone():
                raise HTTPException(409, "Leave your current family before joining another.")
            # Lock group before invitation, matching organizer mutations.
            group = db.execute("""SELECT g.* FROM family_groups g JOIN family_invitations i USING(family_id)
                WHERE i.token_digest=%s AND i.expires_at>NOW() FOR UPDATE OF g""", (digest,)).fetchone()
            if not group:
                raise HTTPException(404, "This invitation is unavailable. Ask for a new invitation.")
            invitation = db.execute("SELECT * FROM family_invitations WHERE token_digest=%s AND expires_at>NOW() FOR UPDATE", (digest,)).fetchone()
            if not invitation or invitation["claimant_id"] not in (None, uid):
                raise HTTPException(404, "This invitation is unavailable. Ask for a new invitation.")
            pending = db.execute("SELECT invitation_id FROM family_invitations WHERE claimant_id=%s AND expires_at>NOW()", (uid,)).fetchone()
            if pending and pending["invitation_id"] != invitation["invitation_id"]:
                raise HTTPException(409, "Cancel your pending request before joining another family.")
            db.execute("DELETE FROM family_invitations WHERE claimant_id=%s AND expires_at<=NOW()", (uid,))
            db.execute("UPDATE family_invitations SET claimant_id=%s,claimant_name=%s WHERE invitation_id=%s", (uid,display_name,invitation["invitation_id"]))

    def resolve_invitation(self, principal, invitation_id, approve):
        with self.transaction(principal) as db:
            group = self.group(db, principal.user.user_id, organizer=True)
            row = db.execute("SELECT * FROM family_invitations WHERE invitation_id=%s AND family_id=%s FOR UPDATE",
                             (invitation_id,group["family_id"])).fetchone()
            if not row:
                if approve:
                    raise HTTPException(409, "This request has changed. Refresh your family.")
                return
            if approve:
                valid = db.execute("SELECT 1 FROM family_invitations WHERE invitation_id=%s AND expires_at>NOW()", (invitation_id,)).fetchone()
                active = db.execute("SELECT 1 FROM users WHERE user_id=%s AND status='active'", (row["claimant_id"],)).fetchone()
                if not valid or not active or not row["claimant_id"]:
                    raise HTTPException(409, "This request is no longer available.")
                count = db.execute("SELECT count(*) AS n FROM family_members WHERE family_id=%s", (group["family_id"],)).fetchone()["n"]
                if count >= 6:
                    raise HTTPException(409, "Your family already has six members.")
                inserted = db.execute("""INSERT INTO family_members(user_id,family_id,display_name) VALUES (%s,%s,%s)
                    ON CONFLICT(user_id) DO NOTHING RETURNING user_id""", (row["claimant_id"],group["family_id"],row["claimant_name"])).fetchone()
                if not inserted:
                    raise HTTPException(409, "This person already belongs to a family.")
            db.execute("DELETE FROM family_invitations WHERE invitation_id=%s", (invitation_id,))

    def rename_member(self, principal, display_name):
        with self.transaction(principal) as db:
            group = self.group(db, principal.user.user_id)
            db.execute("UPDATE family_members SET display_name=%s WHERE family_id=%s AND user_id=%s",
                       (display_name,group["family_id"],principal.user.user_id))

    def rename_family(self, principal, name):
        with self.transaction(principal) as db:
            group = self.group(db, principal.user.user_id, organizer=True)
            db.execute("UPDATE family_groups SET name=%s WHERE family_id=%s", (name,group["family_id"]))

    def propose_handover(self, principal, candidate_id):
        with self.transaction(principal) as db:
            group = self.group(db, principal.user.user_id, organizer=True)
            candidate = db.execute("""SELECT m.user_id FROM family_members m JOIN users u USING(user_id)
                WHERE m.family_id=%s AND m.user_id=%s AND u.status='active'""", (group["family_id"],candidate_id)).fetchone()
            if not candidate or candidate_id == principal.user.user_id:
                raise HTTPException(409, "Choose another active member of your family.")
            db.execute("""INSERT INTO family_organizer_transfers(family_id,transfer_id,candidate_id)
                VALUES (%s,%s,%s) ON CONFLICT(family_id) DO UPDATE SET transfer_id=EXCLUDED.transfer_id,
                candidate_id=EXCLUDED.candidate_id,expires_at=NOW()+INTERVAL '3 days'""",
                (group["family_id"],uuid4(),candidate_id))

    def resolve_handover(self, principal, transfer_id, accept):
        uid = principal.user.user_id
        with self.transaction(principal) as db:
            group = self.group(db, uid)
            transfer = db.execute("""SELECT * FROM family_organizer_transfers
                WHERE family_id=%s AND transfer_id=%s AND expires_at>NOW() FOR UPDATE""",
                (group["family_id"],transfer_id)).fetchone()
            if not transfer:
                raise HTTPException(409, "This handover has changed or expired. Refresh your family.")
            if accept and transfer["candidate_id"] != uid:
                raise HTTPException(404, "Handover not found.")
            if not accept and uid not in (transfer["candidate_id"],group["organizer_id"]):
                raise HTTPException(404, "Handover not found.")
            if accept:
                db.execute("UPDATE family_groups SET organizer_id=%s WHERE family_id=%s", (uid,group["family_id"]))
            db.execute("DELETE FROM family_organizer_transfers WHERE family_id=%s", (group["family_id"],))

    def remove(self, principal, member_id):
        uid = principal.user.user_id
        with self.transaction(principal) as db:
            group = self.group(db, uid)
            if member_id == group["organizer_id"]:
                raise HTTPException(409, "The organizer must close the family instead.")
            if uid != member_id and uid != group["organizer_id"]:
                raise HTTPException(404, "Member not found.")
            db.execute("DELETE FROM family_members WHERE family_id=%s AND user_id=%s", (group["family_id"],member_id))

    def close(self, principal):
        with self.transaction(principal) as db:
            group = self.group(db, principal.user.user_id, organizer=True)
            db.execute("DELETE FROM family_groups WHERE family_id=%s", (group["family_id"],))

    def cancel_request(self, principal):
        with self.transaction(principal) as db:
            db.execute("DELETE FROM family_invitations WHERE claimant_id=%s", (principal.user.user_id,))

    def library(self, principal, after_profile_id=None, after_memory_id=None):
        with self.transaction(principal) as db:
            group = self.group(db, principal.user.user_id)
            rows = db.execute("""SELECT c.profile_id,c.memory_id,c.author_id,c.revision,c.payload,c.updated_at,
                c.allows_family_edits,c.last_editor_id,editor.display_name AS last_editor_name,
                m.display_name AS author_name FROM family_content c
                JOIN family_members m ON m.user_id=c.author_id AND m.family_id=%s
                LEFT JOIN family_members editor ON editor.user_id=c.last_editor_id AND editor.family_id=c.family_id
                JOIN users u ON u.user_id=c.author_id AND u.status='active'
                JOIN profile_memberships pm ON pm.profile_id=c.profile_id AND pm.user_id=c.author_id AND pm.status='active' AND pm.role='owner'
                WHERE ((c.family_id=%s AND c.payload IS NOT NULL) OR (c.author_id=%s AND c.payload IS NULL))
                AND NOT EXISTS (SELECT 1 FROM digital_human_profile_erasure_requests e WHERE e.profile_id=c.profile_id)
                AND (%s::uuid IS NULL OR (c.profile_id,c.memory_id)>(%s::uuid,%s::uuid))
                ORDER BY c.profile_id,c.memory_id LIMIT 51""",
                (group["family_id"],group["family_id"],principal.user.user_id,
                 after_profile_id,after_profile_id,after_memory_id)).fetchall()
            more = len(rows) > 50
            items = rows[:50]
            return {"family_id": group["family_id"], "items": items, "next_profile_id": items[-1]["profile_id"] if more else None,
                    "next_memory_id": items[-1]["memory_id"] if more else None}

    @staticmethod
    def owned_content_profile(db, uid, profile_id, memory_id):
        profile = db.execute("""SELECT p.profile_id FROM digital_human_profiles p
            JOIN profile_memberships m USING(profile_id)
            WHERE p.profile_id=%s AND m.user_id=%s AND m.status='active' AND m.role='owner'
            AND NOT EXISTS (SELECT 1 FROM digital_human_profile_erasure_requests e WHERE e.profile_id=p.profile_id)
            FOR SHARE OF p,m""", (profile_id,uid)).fetchone()
        if not profile:
            raise HTTPException(404, "Memory Space not found.")
        db.execute("SELECT generation FROM memory_index_generations WHERE profile_id=%s FOR UPDATE", (profile_id,))
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 027))", (f"{profile_id}:{memory_id}",))
        if db.execute("SELECT 1 FROM memory_deletion_tombstones WHERE profile_id=%s AND memory_id=%s",
                      (profile_id,str(memory_id))).fetchone():
            raise HTTPException(409, "This memory was deleted and cannot be shared.")

    def publish_content(self, principal, profile_id, memory_id, body):
        uid = principal.user.user_id
        with self.transaction(principal) as db:
            group = self.group(db, uid)
            if group["family_id"] != body.family_id:
                raise HTTPException(409, "Your family changed. Review the recipients before sharing.")
            self.owned_content_profile(db, uid, profile_id, memory_id)
            if body.asset_id is not None:
                self.gallery_media(profile_id, str(body.asset_id), body.kind)
            current = db.execute("SELECT * FROM family_content WHERE profile_id=%s AND memory_id=%s FOR UPDATE",
                                 (profile_id,memory_id)).fetchone()
            if current and current["author_id"] != uid:
                raise HTTPException(404, "Shared item not found.")
            revision = current["revision"] if current else 0
            payload = body.model_dump(mode="json", exclude={"expected_revision", "family_id"})
            if revision != body.expected_revision:
                # A retry after a lost acknowledgement is safe only for the identical publication.
                if (current and current["family_id"] == group["family_id"] and current["payload"] == payload
                        and revision == body.expected_revision + 1):
                    return {"revision": revision}
                raise HTTPException(409, "This shared item changed. Refresh before sharing your version.")
            db.execute("""INSERT INTO family_content(profile_id,memory_id,author_id,family_id,revision,payload,last_editor_id)
                VALUES (%s,%s,%s,%s,1,%s,%s) ON CONFLICT(profile_id,memory_id) DO UPDATE
                SET family_id=EXCLUDED.family_id,revision=family_content.revision+1,payload=EXCLUDED.payload,
                last_editor_id=EXCLUDED.last_editor_id,
                allows_family_edits=CASE WHEN family_content.family_id=EXCLUDED.family_id AND family_content.payload IS NOT NULL
                    THEN family_content.allows_family_edits ELSE FALSE END,updated_at=NOW()""",
                (profile_id,memory_id,uid,group["family_id"],Jsonb(payload),uid))
            return {"revision": revision+1}

    def withdraw_content(self, principal, profile_id, memory_id, expected_revision):
        with self.transaction(principal) as db:
            self.group(db, principal.user.user_id)
            row = db.execute("""SELECT * FROM family_content WHERE profile_id=%s AND memory_id=%s
                AND author_id=%s FOR UPDATE""", (profile_id,memory_id,principal.user.user_id)).fetchone()
            if not row:
                raise HTTPException(404, "Shared item not found.")
            if row["payload"] is None and row["revision"] == expected_revision + 1:
                return {"revision": row["revision"]}
            if row["revision"] != expected_revision:
                raise HTTPException(409, "This shared item changed. Refresh before removing it.")
            db.execute("""UPDATE family_content SET family_id=NULL,payload=NULL,revision=revision+1,updated_at=NOW()
                WHERE profile_id=%s AND memory_id=%s""", (profile_id,memory_id))
            return {"revision": expected_revision+1}

    def content_media(self, principal, profile_id, memory_id, expected_revision):
        with self.transaction(principal) as db:
            group = self.group(db, principal.user.user_id)
            row = self.read_content(db, group["family_id"], profile_id, memory_id, expected_revision)
            if row["payload"]["kind"] == "story":
                raise HTTPException(404, "Shared media not found.")
            return self.gallery_media(profile_id, row["payload"]["asset_id"], row["payload"]["kind"])

    def content_item(self, principal, profile_id, memory_id, expected_revision):
        with self.transaction(principal) as db:
            group = self.group(db, principal.user.user_id)
            return self.read_content(db, group["family_id"], profile_id, memory_id, expected_revision)

    def edit_content(self, principal, profile_id, memory_id, body):
        with self.transaction(principal) as db:
            group = self.group(db, principal.user.user_id)
            if group["family_id"] != body.family_id:
                raise HTTPException(409, "Your family changed. Reopen Family before editing.")
            row = self.read_content(db, group["family_id"], profile_id, memory_id, body.expected_revision)
            if row["author_id"] != principal.user.user_id and not row["allows_family_edits"]:
                raise HTTPException(403, "Only the author can edit this moment.")
            title, text = body.title.strip(), body.text.strip()
            if not title or (row["payload"]["kind"] == "story" and not text):
                raise HTTPException(422, "A shared story needs a title and text.")
            payload = {**row["payload"], "title": title, "text": text}
            if payload == row["payload"]:
                return row
            db.execute("""UPDATE family_content SET payload=%s,revision=revision+1,last_editor_id=%s,updated_at=NOW()
                WHERE profile_id=%s AND memory_id=%s""",
                (Jsonb(payload),principal.user.user_id,profile_id,memory_id))
            return self.read_content(db, group["family_id"], profile_id, memory_id, body.expected_revision+1)

    def update_collaboration(self, principal, profile_id, memory_id, body):
        with self.transaction(principal) as db:
            group = self.group(db, principal.user.user_id)
            if group["family_id"] != body.family_id:
                raise HTTPException(409, "Your family changed. Reopen Family before editing.")
            row = self.read_content(db, group["family_id"], profile_id, memory_id, body.expected_revision)
            if row["author_id"] != principal.user.user_id:
                raise HTTPException(403, "Only the author can change editing permissions.")
            if row["allows_family_edits"] == body.allows_family_edits:
                return row
            db.execute("""UPDATE family_content SET allows_family_edits=%s,revision=revision+1,updated_at=NOW()
                WHERE profile_id=%s AND memory_id=%s""", (body.allows_family_edits,profile_id,memory_id))
            return self.read_content(db, group["family_id"], profile_id, memory_id, body.expected_revision+1)

    @staticmethod
    def read_content(db, family_id, profile_id, memory_id, expected_revision):
        row = db.execute("""SELECT c.profile_id,c.memory_id,c.author_id,c.payload,c.revision,m.display_name AS author_name,
                c.allows_family_edits,c.last_editor_id,editor.display_name AS last_editor_name
                FROM family_content c
                JOIN family_members m ON m.user_id=c.author_id AND m.family_id=c.family_id
                LEFT JOIN family_members editor ON editor.user_id=c.last_editor_id AND editor.family_id=c.family_id
                JOIN users u ON u.user_id=c.author_id AND u.status='active'
                JOIN profile_memberships pm ON pm.profile_id=c.profile_id AND pm.user_id=c.author_id AND pm.status='active' AND pm.role='owner'
                WHERE c.profile_id=%s AND c.memory_id=%s AND c.family_id=%s AND c.payload IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM digital_human_profile_erasure_requests e WHERE e.profile_id=c.profile_id)
                FOR SHARE OF c""", (profile_id,memory_id,family_id)).fetchone()
        if not row:
            raise HTTPException(404, "This shared moment is no longer available.")
        if expected_revision is not None and row["revision"] != expected_revision:
            raise HTTPException(409, "This shared item changed. Refresh before opening it.")
        return row

    @staticmethod
    def gallery_media(profile_id, asset_id, kind):
        from app.services.avatar_media_storage_service import AvatarMediaStorageService, AvatarMediaAssetNotFoundError
        try:
            media = AvatarMediaStorageService().get_metadata(asset_id)
        except AvatarMediaAssetNotFoundError as error:
            raise HTTPException(404, "This media is no longer available. Check the original in Gallery.") from error
        if UUID(media.profile_id) != profile_id or media.asset_type != (
            "memory_image" if kind == "photo" else "memory_video"
        ):
            raise HTTPException(404, "Gallery item not found.")
        if not Path(media.storage_path).is_file():
            raise HTTPException(409, "The original media is unavailable. Upload it again in Gallery before sharing.")
        return media

    def purge_expired(self):
        with psycopg.connect(self.database_url, connect_timeout=10) as db:
            db.execute("SET LOCAL statement_timeout = '15s'")
            db.execute("""DELETE FROM family_invitations WHERE invitation_id IN (
                SELECT invitation_id FROM family_invitations WHERE expires_at<=NOW()
                ORDER BY expires_at LIMIT 1000 FOR UPDATE SKIP LOCKED)""")
            db.execute("""DELETE FROM family_organizer_transfers WHERE family_id IN (
                SELECT family_id FROM family_organizer_transfers WHERE expires_at<=NOW()
                ORDER BY expires_at LIMIT 1000 FOR UPDATE SKIP LOCKED)""")
