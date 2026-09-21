import asyncio
import logging
import os

from app.services.account_erasure_service import AccountErasureService
from app.services.profile_erasure_service import ProfileErasureService
from app.services.deletion_retention_service import DeletionRetentionService


logger = logging.getLogger(__name__)


async def recover_erasures_once() -> None:
    for factory in (ProfileErasureService, AccountErasureService):
        try:
            await factory().resume_pending(limit=10)
        except Exception:
            logger.error("Deletion recovery is unavailable; pending requests remain queued.")
    try:
        await asyncio.to_thread(
            DeletionRetentionService(database_url=os.environ["DATABASE_URL"]).purge_completed_cleanup_records
        )
    except Exception:
        logger.error("Completed cleanup record retention will be retried.")


async def run_erasure_recovery() -> None:
    while True:
        await recover_erasures_once()
        await asyncio.sleep(30)
