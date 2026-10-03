"""Private, encrypted references governed by the canonical voice-job locks."""

import base64
import json
import os
from uuid import UUID

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from psycopg.rows import dict_row

from app.schemas.self_hosted_voice import SelfHostedVoiceRequest
from app.services.digital_human_profile_repository import (
    DigitalHumanProfileRepository, StaleVoiceTrainingError,
)


class VoiceReferenceRepository:
    def __init__(self, repository=None, *, keys=None, active_key_id=None):
        self.repository = repository or DigitalHumanProfileRepository()
        configured = keys if keys is not None else json.loads(os.environ.get("STAY_VOICE_REFERENCE_KEYS", "{}"))
        self.active_key_id = active_key_id or os.environ.get("STAY_VOICE_REFERENCE_ACTIVE_KEY", "")
        if not isinstance(configured, dict) or self.active_key_id not in configured:
            raise ValueError("Voice reference encryption is not configured")
        self.ciphers = {}
        for key_id, encoded in configured.items():
            if not isinstance(key_id, str) or not 1 <= len(key_id) <= 64:
                raise ValueError("Invalid voice reference key identifier")
            key = base64.b64decode(encoded, validate=True)
            if len(key) != 32:
                raise ValueError("Voice reference encryption requires 256-bit keys")
            self.ciphers[key_id] = AESGCM(key)

    @staticmethod
    def _binding(profile_id, job_id, revision, model_revision):
        return json.dumps(["stay-voice-reference-v1", str(profile_id), str(job_id), revision,
                           model_revision], separators=(",", ":")).encode()

    def put(self, request: SelfHostedVoiceRequest):
        audio = request.reference_audio()
        nonce = os.urandom(12)
        encrypted = nonce + self.ciphers[self.active_key_id].encrypt(nonce, audio,
            self._binding(request.profile_id, request.voice_version, request.consent_revision,
                          request.model_revision))
        with psycopg.connect(self.repository.database_url, row_factory=dict_row, connect_timeout=10) as db:
            with db.cursor() as cursor:
                profile, job = self.repository._lock_voice_job(cursor, request.profile_id, request.voice_version)
                self.repository._require_profile_write_allowed_with_cursor(cursor, request.profile_id)
                if (job['provider'] != 'stay_voice' or job['status'] not in {'submitted', 'training'}
                        or (job['request_payload'] or {}).get('_stay_consent_revision') != request.consent_revision
                        or (job['request_payload'] or {}).get('_stay_voice_selection') != self.repository._voice_activation_snapshot(profile)
                        or not self.repository._voice_consent_matches(cursor, request.profile_id, request.consent_revision)):
                    raise StaleVoiceTrainingError("Voice preparation is no longer authorized")
                cursor.execute("SELECT * FROM self_hosted_voice_references WHERE job_id=%s FOR UPDATE",
                               (request.voice_version,))
                existing = cursor.fetchone()
                if existing:
                    if (existing['profile_id'] != request.profile_id
                            or existing['consent_revision'] != request.consent_revision
                            or existing['model_revision'] != request.model_revision
                            or self._decode(existing) != audio):
                        raise StaleVoiceTrainingError("A voice version cannot change its reference")
                    return
                cursor.execute("""INSERT INTO self_hosted_voice_references
                    (job_id,profile_id,consent_revision,model_revision,encryption_key_id,ciphertext)
                    VALUES (%s,%s,%s,%s,%s,%s)""", (request.voice_version,request.profile_id,
                        request.consent_revision,request.model_revision,self.active_key_id,encrypted))

    def get(self, *, profile_id: UUID, job_id: UUID):
        with psycopg.connect(self.repository.database_url, row_factory=dict_row, connect_timeout=10) as db:
            with db.cursor() as cursor:
                profile, job = self.repository._lock_voice_job(cursor, profile_id, job_id)
                self.repository._require_profile_write_allowed_with_cursor(cursor, profile_id)
                cursor.execute("SELECT * FROM self_hosted_voice_references WHERE job_id=%s AND profile_id=%s",
                               (job_id,profile_id))
                row = cursor.fetchone()
                if (not row or job['provider'] != 'stay_voice' or job['status'] != 'ready'
                        or job['provider_job_id'] != str(job_id)
                        or not profile['consent_verified'] or not profile['voice_id']
                        or (job['provider_payload'] or {}).get('_stay_activated') is not True
                        or not self.repository._voice_consent_matches(cursor, profile_id, row['consent_revision'])):
                    raise StaleVoiceTrainingError("This voice reference is no longer available")
                return self._decode(row), row['model_revision'], row['consent_revision']

    def _decode(self, row):
        cipher = self.ciphers.get(row['encryption_key_id'])
        if cipher is None:
            raise ValueError("The voice reference encryption key is unavailable")
        encrypted = bytes(row['ciphertext'])
        try:
            return cipher.decrypt(encrypted[:12], encrypted[12:], self._binding(row['profile_id'],
                row['job_id'], row['consent_revision'], row['model_revision']))
        except InvalidTag:
            raise ValueError("The voice reference could not be authenticated") from None

    @staticmethod
    def delete_profile(repository, *, profile_id: UUID):
        with psycopg.connect(repository.database_url, row_factory=dict_row, connect_timeout=10) as db:
            with db.cursor() as cursor:
                repository._lock_profile_scope(cursor, profile_id)
                cursor.execute('SELECT profile_id FROM digital_human_profiles WHERE profile_id=%s FOR UPDATE', (profile_id,))
                cursor.execute("DELETE FROM self_hosted_voice_references WHERE profile_id=%s", (profile_id,))
                cursor.execute("""UPDATE digital_human_training_jobs SET status='deleted'
                    WHERE profile_id=%s AND training_type='voice' AND provider='stay_voice'""", (profile_id,))
                cursor.execute("""UPDATE digital_human_profiles SET voice_id=NULL,voice_provider=NULL,
                    voice_training_job_id=NULL,voice_training_status='not_started',voice_ready_at=NULL
                    WHERE profile_id=%s AND voice_provider='stay_voice'""", (profile_id,))

    @staticmethod
    def delete(repository, *, profile_id: UUID, job_id: UUID):
        # Deletion needs no encryption key and remains possible during a key outage.
        with psycopg.connect(repository.database_url, row_factory=dict_row, connect_timeout=10) as db:
            with db.cursor() as cursor:
                _, job = repository._lock_voice_job(cursor, profile_id, job_id)
                if job['provider'] != 'stay_voice':
                    raise StaleVoiceTrainingError("This job belongs to another voice provider")
                cursor.execute("DELETE FROM self_hosted_voice_references WHERE job_id=%s AND profile_id=%s",
                               (job_id,profile_id))
                cursor.execute("UPDATE digital_human_training_jobs SET status='deleted' WHERE job_id=%s", (job_id,))
                cursor.execute("""UPDATE digital_human_profiles SET voice_id=NULL,voice_provider=NULL,
                    voice_training_job_id=NULL,voice_training_status='not_started',voice_ready_at=NULL
                    WHERE profile_id=%s AND voice_provider='stay_voice' AND voice_training_job_id=%s""",
                    (profile_id,str(job_id)))
