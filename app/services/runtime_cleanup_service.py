import asyncio
import logging

from app.services.runtime_cleanup_repository import RuntimeCleanupRepository
from app.services.tavus_runtime_correlation import find_correlated_conversation


class RuntimeCleanupService:
    def __init__(self, repository=None, adapter=None, provider=None):
        from app.services.avatar_runtime_tavus_adapter import AvatarRuntimeTavusAdapter
        from app.services.avatar_provider_service import AvatarProviderService
        self.repository = repository or RuntimeCleanupRepository()
        self.adapter = adapter or AvatarRuntimeTavusAdapter()
        self.provider = provider or AvatarProviderService()

    async def recover_once(self):
        row = await asyncio.to_thread(self.repository.claim)
        if row is None:
            return False
        success = False
        try:
            # Remove transport even if the provider cannot currently confirm termination.
            transport_closed = False
            try:
                await asyncio.wait_for(self.adapter._delete_remote_resources(
                    room_name=row["room_name"], dispatch_id=row["dispatch_id"]), timeout=30)
                transport_closed = True
            except Exception:
                logging.getLogger(__name__).warning("Runtime transport cleanup remains pending.")
            current = await asyncio.to_thread(self.repository.get, row["session_id"])
            if (current["provider_create_started"] or current['conversation_id']) and not current["tavus_ended"]:
                if not current["conversation_id"]:
                    identifier = await asyncio.wait_for(find_correlated_conversation(
                        current['conversation_name']), timeout=30)
                    await asyncio.to_thread(self.repository.conversation, row['session_id'], identifier)
                    current['conversation_id'] = identifier
                await asyncio.wait_for(self.provider.end_tavus_conversation(
                    conversation_id=current["conversation_id"]), timeout=45)
                await asyncio.to_thread(self.repository.ended, row["session_id"], current["conversation_id"])
            if current['conversation_id'] and not current['conversation_deleted']:
                await asyncio.wait_for(self.provider.delete_tavus_conversation(
                    conversation_id=current['conversation_id']), timeout=30)
                await asyncio.to_thread(self.repository.deleted, row['session_id'], current['conversation_id'])
            success = transport_closed
        except Exception:
            logging.getLogger(__name__).warning("Runtime cleanup remains pending; automatic retry scheduled.")
        finally:
            await asyncio.to_thread(self.repository.finish, row, success)
        return True

    async def run(self):
        while True:
            try:
                if await self.recover_once():
                    continue
            except Exception:
                logging.getLogger(__name__).error("Runtime cleanup storage is unavailable; recovery will retry.")
            await asyncio.sleep(5)
