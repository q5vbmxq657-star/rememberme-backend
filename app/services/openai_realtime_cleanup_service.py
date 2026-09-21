import asyncio
import logging

from app.services.openai_realtime_registry import OpenAIRealtimeRegistry
from app.services.openai_realtime_service import openai_realtime_service


class OpenAIRealtimeCleanupService:
    """Parent lifespan must run/cancel run(); acknowledgements are not closure proof."""
    def __init__(self, registry=None, provider=None):
        self.registry = registry or OpenAIRealtimeRegistry()
        self.provider = provider or openai_realtime_service

    async def recover_once(self):
        await asyncio.to_thread(self.registry.sweep)
        row = await asyncio.to_thread(self.registry.claim)
        if row is None:
            return False
        acknowledged = False
        try:
            await self.provider.hangup_call(row['call_id'])
            acknowledged = True
        except Exception:
            logging.getLogger(__name__).warning('OpenAI call cleanup pending; retry scheduled.')
        finally:
            await asyncio.to_thread(self.registry.finish, row, acknowledged)
        return True

    async def run(self):
        while True:
            try:
                if await self.recover_once():
                    continue
            except Exception:
                logging.getLogger(__name__).warning('OpenAI cleanup storage unavailable; retry scheduled.')
            await asyncio.sleep(5)
