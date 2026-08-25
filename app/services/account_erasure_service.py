from __future__ import annotations

from uuid import UUID

from app.security.apple_identity import AppleIdentityVerifier, AppleRefreshTokenCipher
from app.services.profile_erasure_service import ProfileErasureService
from app.services.profile_membership_repository import ProfileMembershipRepository
from app.services.user_identity_repository import UserIdentityRepository


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
    ) -> None:
        self.identity_repository = identity_repository or UserIdentityRepository()
        self.membership_repository = membership_repository or ProfileMembershipRepository()
        self.profile_erasure_service = profile_erasure_service or ProfileErasureService()
        self.apple_verifier = apple_verifier or AppleIdentityVerifier()
        self.apple_cipher = apple_cipher or AppleRefreshTokenCipher()

    async def erase_account(self, *, user_id: UUID) -> None:
        memberships = self.membership_repository.list_owned_for_user(
            user_id=user_id
        )
        for membership in memberships:
            await self.profile_erasure_service.erase_profile(
                profile_id=membership.profile_id,
                idempotency_key=f"account:{user_id}:profile:{membership.profile_id}",
            )

        encrypted = self.identity_repository.get_apple_refresh_credential(
            user_id=user_id
        )
        if encrypted is None:
            raise AccountErasureServiceError(
                "Apple revocation credential is unavailable."
            )

        refresh_token = self.apple_cipher.decrypt(bytes(encrypted))
        await self.apple_verifier.revoke_refresh_token(refresh_token)

        if not self.identity_repository.delete_user_after_profile_erasure(
            user_id=user_id
        ):
            raise AccountErasureServiceError(
                "Account deletion could not be verified."
            )
