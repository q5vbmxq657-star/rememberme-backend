from uuid import UUID

import psycopg
from fastapi import HTTPException

from app.schemas.profile_consent import CONSENT_POLICY_VERSION, PurposeConsentSnapshot
from app.services.profile_consent_repository import ProfileConsentRepository


def require_profile_purposes(profile_id: UUID | str, purposes: set[str], *,
                            expected_revision: int | None = None,
                            database_url: str | None = None) -> PurposeConsentSnapshot:
    try:
        snapshot = ProfileConsentRepository(database_url=database_url).read(UUID(str(profile_id)))
    except ValueError as error:
        raise HTTPException(404, "Profile not found.") from error
    except (psycopg.Error, KeyError) as error:
        raise HTTPException(503, "Permissions are temporarily unavailable.") from error
    if (snapshot.policy_version != CONSENT_POLICY_VERSION
            or not (purposes | {"provider_processing"}).issubset(snapshot.purposes)):
        raise HTTPException(403, detail={"code": "purpose_consent_required",
            "message": "Review permission for this profile before continuing."})
    if expected_revision is not None and snapshot.revision != expected_revision:
        raise HTTPException(409, "Permissions changed. Please start again.")
    return snapshot
