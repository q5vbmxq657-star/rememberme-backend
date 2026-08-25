from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.services.account_erasure_service import AccountErasureService


class IdentityRepository:
    def __init__(self):
        self.deleted_user_id = None

    def get_apple_refresh_credential(self, *, user_id):
        return b"encrypted"

    def delete_user_after_profile_erasure(self, *, user_id):
        self.deleted_user_id = user_id
        return True


class MembershipRepository:
    def __init__(self, profile_ids):
        self.profile_ids = profile_ids

    def list_owned_for_user(self, *, user_id):
        return [SimpleNamespace(profile_id=value) for value in self.profile_ids]


class ProfileErasure:
    def __init__(self):
        self.calls = []

    async def erase_profile(self, *, profile_id, idempotency_key):
        self.calls.append((profile_id, idempotency_key))


class FailingProfileErasure(ProfileErasure):
    async def erase_profile(self, *, profile_id, idempotency_key):
        await super().erase_profile(
            profile_id=profile_id,
            idempotency_key=idempotency_key,
        )
        raise RuntimeError("provider cleanup failed")


class Cipher:
    def decrypt(self, payload):
        assert payload == b"encrypted"
        return "apple-refresh"


class AppleVerifier:
    def __init__(self):
        self.revoked = None

    async def revoke_refresh_token(self, token):
        self.revoked = token


def test_account_erasure_orders_profiles_before_identity_deletion():
    user_id = uuid4()
    profile_ids = [uuid4(), uuid4()]
    identity = IdentityRepository()
    profiles = ProfileErasure()
    apple = AppleVerifier()
    service = AccountErasureService(
        identity_repository=identity,
        membership_repository=MembershipRepository(profile_ids),
        profile_erasure_service=profiles,
        apple_verifier=apple,
        apple_cipher=Cipher(),
    )

    asyncio.run(service.erase_account(user_id=user_id))

    assert [call[0] for call in profiles.calls] == profile_ids
    assert all(str(user_id) in call[1] for call in profiles.calls)
    assert apple.revoked == "apple-refresh"
    assert identity.deleted_user_id == user_id


def test_account_identity_survives_incomplete_profile_erasure():
    user_id = uuid4()
    identity = IdentityRepository()
    service = AccountErasureService(
        identity_repository=identity,
        membership_repository=MembershipRepository([uuid4()]),
        profile_erasure_service=FailingProfileErasure(),
        apple_verifier=AppleVerifier(),
        apple_cipher=Cipher(),
    )

    try:
        asyncio.run(service.erase_account(user_id=user_id))
    except RuntimeError:
        pass
    else:
        raise AssertionError("Incomplete erasure must fail closed.")

    assert identity.deleted_user_id is None
