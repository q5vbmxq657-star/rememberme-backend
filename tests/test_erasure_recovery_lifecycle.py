import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.services import erasure_recovery_service as recovery


def test_profile_failure_does_not_stop_account_recovery_or_retention(monkeypatch):
    profiles = AsyncMock(side_effect=RuntimeError("private diagnostics"))
    accounts = AsyncMock(return_value={"completed": 1, "pending": 0})
    retention = Mock(return_value=2)
    monkeypatch.setattr(recovery, "ProfileErasureService", lambda: SimpleNamespace(resume_pending=profiles))
    monkeypatch.setattr(recovery, "AccountErasureService", lambda: SimpleNamespace(resume_pending=accounts))
    monkeypatch.setattr(recovery, "DeletionRetentionService", lambda **kwargs: SimpleNamespace(purge_completed_cleanup_records=retention))
    asyncio.run(recovery.recover_erasures_once())
    profiles.assert_awaited_once_with(limit=10)
    accounts.assert_awaited_once_with(limit=10)
    retention.assert_called_once()
