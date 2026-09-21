from __future__ import annotations

from uuid import UUID
from typing import Literal

from app.security.apple_identity import AppleIdentityVerifier, AppleRefreshTokenCipher
from app.services.profile_erasure_service import ProfileErasureService
from app.services.profile_membership_repository import ProfileMembershipRepository
from app.services.user_identity_repository import UserIdentityRepository
from app.services.account_erasure_repository import AccountErasureRepository


class AccountErasureServiceError(RuntimeError):
    pass


class AccountErasureService:
    """Single server authority for irreversible user-account deletion."""

    def __init__(
        self,
        *,
        identity_repository: UserIdentityRepository | None = None,
        membership_repository: ProfileMembershipRepository | None = None,
        profile_erasure_service: ProfileErasureService | None = None,
        apple_verifier: AppleIdentityVerifier | None = None,
        apple_cipher: AppleRefreshTokenCipher | None = None,
        erasure_repository: AccountErasureRepository | None = None,
    ) -> None:
        self.identity_repository = identity_repository or UserIdentityRepository()
        self.membership_repository = membership_repository or ProfileMembershipRepository()
        self.profile_erasure_service = profile_erasure_service or ProfileErasureService()
        self.apple_verifier = apple_verifier or AppleIdentityVerifier()
        self.apple_cipher = apple_cipher or AppleRefreshTokenCipher()
        database_url = getattr(self.identity_repository, 'database_url', None)
        self.erasure_repository = erasure_repository or AccountErasureRepository(database_url=database_url)

    async def erase_account(self, *, user_id: UUID) -> Literal['completed', 'deletion_pending']:
        self.erasure_repository.begin(user_id)
        try:
            await self._resume(user_id)
            if self.erasure_repository.stage(user_id) == 'completed':
                return 'completed'
        except Exception:
            # The committed request remains owned by the recovery worker even
            # after the user's access has been disabled.
            return 'deletion_pending'
        return 'deletion_pending'

    async def resume_pending(self, *, limit: int = 100) -> dict[str, int]:
        result = {'completed': 0, 'pending': 0}
        for user_id in self.erasure_repository.pending(limit=limit):
            try:
                await self._resume(user_id)
            except Exception:
                result['pending'] += 1
            else:
                result['completed'] += 1
        return result

    async def _resume(self, user_id: UUID) -> None:
        repository = self.erasure_repository
        with repository.claim(user_id) as locked:
            if not locked:
                raise AccountErasureServiceError('Account deletion is already running.')
            try:
                stage = repository.stage(user_id)
                if stage == 'profiles':
                    for membership in self.membership_repository.list_owned_for_user(user_id=user_id):
                        await self.profile_erasure_service.erase_profile(profile_id=membership.profile_id,
                            idempotency_key=f'account:{user_id}:profile:{membership.profile_id}')
                    repository.advance(user_id, 'profiles', 'apple')
                    stage = 'apple'
                if stage == 'apple':
                    encrypted = self.identity_repository.get_apple_refresh_credential(user_id=user_id)
                    if encrypted is None:
                        raise AccountErasureServiceError('Apple revocation credential is unavailable.')
                    await self.apple_verifier.revoke_refresh_token(self.apple_cipher.decrypt(bytes(encrypted)))
                    repository.advance(user_id, 'apple', 'identity')
                    stage = 'identity'
                if stage == 'identity':
                    if self.identity_repository.get_user(user_id=user_id) is not None:
                        if not self.identity_repository.delete_user_after_profile_erasure(user_id=user_id):
                            raise AccountErasureServiceError('Account deletion could not be verified.')
                    repository.advance(user_id, 'identity', 'completed')
            except Exception:
                repository.retry(user_id)
                raise
