"""Narrow integration with the installed LiveKit Tavus API, not a second media path."""
import asyncio
import inspect
import os
import re

import httpx
from livekit.agents import APIConnectOptions
from livekit.plugins import tavus


class CorrelatedTavusAPI:
    def __init__(self, delegate, repository, session_id):
        if 'extra_payload' not in inspect.signature(delegate.create_conversation).parameters:
            raise RuntimeError('Installed Tavus SDK cannot carry the cleanup correlation.')
        self.delegate = delegate
        self.repository = repository
        self.session_id = session_id

    async def create_conversation(self, **kwargs):
        await asyncio.to_thread(self.repository.authorize, self.session_id)
        name = await asyncio.to_thread(self.repository.begin_provider_create, self.session_id)
        # This is an intent record, not proof of remote creation. Never replay POST.
        try:
            conversation_id = await asyncio.wait_for(self.delegate.create_conversation(
                **kwargs, extra_payload={'conversation_name': name}), timeout=45)
        except BaseException:
            await asyncio.to_thread(self.repository.request, self.session_id)
            raise
        await asyncio.to_thread(self.repository.conversation, self.session_id, conversation_id)
        return conversation_id


class CorrelatedAvatarSession(tavus.AvatarSession):
    def __init__(self, *, repository, session_id, **kwargs):
        # Installed TavusAPI iterates range(max_retry); one means exactly one attempt.
        super().__init__(**kwargs, conn_options=APIConnectOptions(max_retry=1, timeout=20))
        # AvatarSession has no public API injection parameter. Keep this SDK seam
        # isolated and contract-tested so incompatible upgrades fail before create.
        self._api = CorrelatedTavusAPI(self._api, repository, session_id)


async def find_correlated_conversation(conversation_name, *, client=None):
    if not isinstance(conversation_name, str) or not re.fullmatch(r'stay_[0-9a-f]{32}', conversation_name):
        raise RuntimeError('No verified conversation correlation is available.')
    key = os.getenv('TAVUS_API_KEY', '').strip()
    if not key:
        raise RuntimeError('Conversation reconciliation is unavailable.')
    owned = client is None
    client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
    matches = set()
    seen = set()
    expected_total = None
    try:
        for page in range(1, 101):
            response = await client.get('https://tavusapi.com/v2/conversations',
                headers={'x-api-key': key}, params={'page': page, 'limit': 100})
            if response.status_code != 200:
                raise RuntimeError('Conversation reconciliation is unavailable.')
            payload = response.json()
            rows = payload.get('data') if isinstance(payload, dict) else None
            total = payload.get('total_count') if isinstance(payload, dict) else None
            if not isinstance(rows, list) or type(total) is not int or total < 0:
                raise RuntimeError('Conversation listing could not be verified.')
            if expected_total is not None and total != expected_total:
                raise RuntimeError('Conversation listing changed during reconciliation.')
            expected_total = total
            for item in rows:
                if (not isinstance(item, dict) or not isinstance(item.get('conversation_id'), str)
                        or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', item['conversation_id'])):
                    raise RuntimeError('Conversation listing could not be verified.')
                if item['conversation_id'] in seen:
                    raise RuntimeError('Conversation pagination overlaps.')
                seen.add(item['conversation_id'])
                if item.get('conversation_name') == conversation_name:
                    matches.add(item['conversation_id'])
            if len(matches) > 1:
                raise RuntimeError('Conversation correlation is ambiguous.')
            if len(seen) > total:
                raise RuntimeError('Conversation listing count is inconsistent.')
            if len(seen) == total:
                if len(matches) == 1:
                    return next(iter(matches))
                raise RuntimeError('Conversation creation outcome remains unverified.')
            if not rows:
                break
        raise RuntimeError('Conversation pagination remains incomplete.')
    except (httpx.HTTPError, ValueError):
        raise RuntimeError('Conversation reconciliation is unavailable.') from None
    finally:
        if owned:
            await client.aclose()
