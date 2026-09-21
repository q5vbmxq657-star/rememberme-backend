from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, ConfigDict, Field

from app.security.profile_authorization import require_profile_access
from app.security.purpose_authorization import require_profile_purposes
from app.security.user_auth import AuthenticatedSessionPrincipal, require_authenticated_principal
from app.services.openai_realtime_service import openai_realtime_service
from app.services.openai_realtime_registry import OpenAIRealtimeRegistry, RealtimeStateConflict
from app.services.memory_chat_retrieval_service import MemoryChatRetrievalService, require_current_memory_evidence
from app.services.pgvector_memory_service import PGVectorMemoryService, PGVectorStaleIndexError

router = APIRouter(prefix='/v1/realtime', tags=['realtime'])
retrieval_service = MemoryChatRetrievalService()


class RealtimeAvatarSessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile_id: UUID
    profile_name: str | None = Field(default=None, max_length=200)
    relationship: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default='de-DE', max_length=80)
    mode: str | None = Field(default='voice', max_length=20)


class RealtimeAvatarSessionResponse(BaseModel):
    session_id: UUID
    profile_id: UUID
    model: str
    voice: str
    session_type: str = 'openai_realtime_avatar'
    connection_mode: str = 'stay_sdp'
    transport: str = 'webrtc'
    fallback_mode: str = 'native_auto_turn_voice'


class RealtimeConnectRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    offer_sdp: str = Field(min_length=5, max_length=131072)


def _owned(registry, session_id, principal):
    row = registry.owned(session_id, principal)
    if row is None:
        raise HTTPException(404, 'Call not found.')
    return row


@router.post('/avatar/session', response_model=RealtimeAvatarSessionResponse)
async def create_realtime_avatar_session(request: RealtimeAvatarSessionRequest,
    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal),
    realtime_protocol: str | None = Header(default=None, alias='X-STAY-Realtime-Protocol')):
    if realtime_protocol != 'stay_sdp':
        raise HTTPException(426, detail={'code': 'app_update_required',
            'message': 'Update STAY to start a live conversation.'})
    try:
        await asyncio.to_thread(require_profile_access, principal=principal, profile_id=request.profile_id)
        consent = await asyncio.to_thread(require_profile_purposes, request.profile_id, {'memory_context'})
        model, voice = openai_realtime_service.configuration()
        row = await asyncio.to_thread(OpenAIRealtimeRegistry().reserve,
            profile_id=request.profile_id, user_id=principal.user.user_id,
            auth_session_id=principal.session_id, purpose_revision=consent.revision,
            model=model, voice=voice, metadata=request.model_dump(mode='json', exclude={'profile_id'}))
        return RealtimeAvatarSessionResponse(session_id=row['session_id'],
            profile_id=request.profile_id, model=model, voice=voice)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, 'Live conversation is temporarily unavailable.') from error


@router.post('/avatar/sessions/{session_id}/connect')
async def connect_realtime_avatar_session(session_id: UUID, request: RealtimeConnectRequest,
    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    registry = OpenAIRealtimeRegistry()
    started = False
    call_id = None
    try:
        row = await asyncio.to_thread(_owned, registry, session_id, principal)
        profile_id = str(row['profile_id'])
        await asyncio.to_thread(require_profile_access, principal=principal, profile_id=profile_id)
        await asyncio.to_thread(require_profile_purposes, profile_id, {'memory_context'}, expected_revision=row['purpose_revision'])
        if not request.offer_sdp.lstrip().startswith('v=0'):
            raise HTTPException(422, 'A valid SDP offer is required.')
        vector = PGVectorMemoryService()
        version = await asyncio.to_thread(vector.evidence_version, profile_id)
        memories = await asyncio.to_thread(retrieval_service.retrieve, profile_id=profile_id,
            user_message='Greeting, preferred form of address, nickname and shared memories',
            recent_messages=[], retrieval_limit=8)

        def authorize():
            require_profile_access(principal=principal, profile_id=profile_id)
            require_profile_purposes(profile_id, {'memory_context'}, expected_revision=row['purpose_revision'])
            vector.require_evidence_version(profile_id, version)
            require_current_memory_evidence(memories, profile_id=profile_id)

        await asyncio.to_thread(authorize)
        memory_version = [version[0], version[1], str(version[2])] if version else None
        await asyncio.to_thread(registry.begin, session_id, memory_version)
        started = True
        await asyncio.to_thread(authorize)
        metadata = row['metadata']
        instructions = openai_realtime_service._build_avatar_instructions(
            profile_id=profile_id, profile_name=metadata.get('profile_name'),
            relationship=metadata.get('relationship'), language=metadata.get('language'),
            mode=metadata.get('mode'), persona_context=None, instructions=None,
            confirmed_address=getattr(memories, 'confirmed_address', None),
            memory_context=json.dumps([item.model_dump(mode='json') for item in memories], ensure_ascii=False))
        async def create_and_register():
            nonlocal call_id
            call_id, answer = await openai_realtime_service.create_call(offer_sdp=request.offer_sdp,
                model=row['model'], voice=row['voice'], instructions=instructions)
            registered = await asyncio.to_thread(registry.register, session_id, call_id)
            return registered, answer
        creation = asyncio.create_task(create_and_register())
        try:
            registered, answer = await asyncio.shield(creation)
        except asyncio.CancelledError:
            # Resolve the bounded create operation so a disconnected client cannot orphan its handle.
            try:
                await creation
            except Exception:
                pass
            raise
        await asyncio.to_thread(authorize)
        if registered['hangup_requested_at'] is not None or not answer.lstrip().startswith('v=0'):
            raise RealtimeStateConflict('Call is no longer available.')
        current = await asyncio.to_thread(_owned, registry, session_id, principal)
        if current['hangup_requested_at'] is not None:
            raise RealtimeStateConflict('Call is closing.')
        return {'session_id': session_id, 'profile_id': row['profile_id'], 'answer_sdp': answer}
    except BaseException as error:
        if started:
            async def record_cleanup():
                try:
                    await asyncio.to_thread(registry.request if call_id else registry.unknown, session_id)
                finally:
                    if call_id:
                        try:
                            await openai_realtime_service.hangup_call(call_id)
                            await asyncio.to_thread(registry.acknowledge, session_id, call_id)
                        except Exception:
                            pass
            try:
                await asyncio.shield(record_cleanup())
            except Exception:
                # Cleanup storage failure must not replace the original safe response.
                pass
        if isinstance(error, (HTTPException, asyncio.CancelledError)):
            raise
        if isinstance(error, (RealtimeStateConflict, PGVectorStaleIndexError)):
            raise HTTPException(409, 'Call settings changed. Start a new call.') from error
        raise HTTPException(503, 'Live conversation is temporarily unavailable.') from error


@router.delete('/avatar/sessions/{session_id}', status_code=202)
async def delete_realtime_avatar_session(session_id: UUID,
    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    registry = OpenAIRealtimeRegistry()
    # Ownership survives profile erasure; purpose/profile access must not block cleanup.
    try:
        await asyncio.to_thread(_owned, registry, session_id, principal)
        row = await asyncio.to_thread(registry.request, session_id)
        if row is None:
            raise HTTPException(404, 'Call not found.')
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, 'Call cleanup could not be saved. Please try again.') from error

    call_id = row.get('call_id')
    if call_id and row['hangup_acknowledged_at'] is None:
        try:
            # Persist first: timeout, disconnect and process exit remain worker-retryable.
            await asyncio.wait_for(openai_realtime_service.hangup_call(call_id), timeout=3.0)
            await asyncio.to_thread(registry.acknowledge, session_id, call_id)
            row = await asyncio.to_thread(_owned, registry, session_id, principal)
        except Exception:
            # Accepted cleanup intent is not proof that the remote call has ended.
            pass
    return {'session_id': session_id, 'profile_id': row['profile_id'], 'state': row['state'],
        'hangup_acknowledged': row['hangup_acknowledged_at'] is not None}


@router.get('/health')
async def realtime_health():
    return {'status': 'ok', 'service': 'openai-realtime', 'connection_mode': 'stay_sdp',
        'session_endpoint': '/v1/realtime/avatar/session', 'transport': 'webrtc'}
