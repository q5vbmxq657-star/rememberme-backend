"""Fail deployment before changing voice routing with an incompatible runtime."""

import asyncio
import os
import re

import psycopg

from app.services.digital_human_profile_repository import DigitalHumanProfileRepository
from app.services.self_hosted_voice_client import SelfHostedVoiceClient
from app.services.voice_reference_repository import VoiceReferenceRepository


def stored_requirements(repository):
    with psycopg.connect(repository.database_url, connect_timeout=10) as db:
        with db.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '10s'")
            cursor.execute("SELECT DISTINCT encryption_key_id FROM self_hosted_voice_references")
            keys = {row[0] for row in cursor.fetchall()}
            cursor.execute("""SELECT DISTINCT r.model_revision FROM self_hosted_voice_references r
                JOIN digital_human_training_jobs j ON j.job_id=r.job_id
                WHERE j.status='ready' AND j.provider_payload->>'_stay_activated'='true'""")
            revisions = {row[0] for row in cursor.fetchall()}
    return keys, revisions


async def verify_voice_release(repository=None):
    provider = os.getenv("STAY_VOICE_TRAINING_PROVIDER", "elevenlabs").strip()
    if provider not in {"elevenlabs", "stay_voice"}:
        raise ValueError("Unsupported voice training provider")
    repository = repository or DigitalHumanProfileRepository()
    keys, revisions = await asyncio.to_thread(stored_requirements, repository)
    if provider != "stay_voice" and not keys:
        return
    references = VoiceReferenceRepository(repository)
    if not keys.issubset(references.ciphers):
        raise ValueError("A retained voice reference key is missing")
    revision = os.getenv("STAY_VOICE_MODEL_REVISION", "")
    if not re.fullmatch(r"[a-f0-9]{64}", revision):
        raise ValueError("Voice model revision is not configured")
    if revisions - {revision}:
        raise ValueError("The selected runtime cannot serve existing voice model revisions")
    client = SelfHostedVoiceClient(os.getenv("STAY_VOICE_RUNTIME_URL", ""),
                                   os.getenv("STAY_VOICE_RUNTIME_TOKEN", ""))
    await client.verify_readiness(revision)


def main():
    try:
        asyncio.run(verify_voice_release())
    except Exception:
        # Configuration/parser exceptions may contain secrets. Never dump them.
        print("Voice release preflight failed: verify retained keys, model revision and runtime readiness.")
        return 1
    print("Voice release configuration verified; GPU quality/device acceptance is still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
