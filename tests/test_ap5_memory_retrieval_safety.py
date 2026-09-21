from unittest.mock import Mock
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException

from app.services.memory_chat_retrieval_service import MemoryChatRetrievalService
from app.services.pgvector_memory_service import PGVectorStaleIndexError
from app.schemas.vector_memory import SearchMemoryRequest
from test_memory_index_generation_integration import database, service, snapshot


@pytest.mark.parametrize('error', [HTTPException(403), HTTPException(409), HTTPException(503), PGVectorStaleIndexError('stale')])
def test_security_rejection_does_not_enter_lexical_fallback(error):
    vector = Mock()
    vector.search.side_effect = error
    retrieval = MemoryChatRetrievalService(service_factory=lambda: vector)
    with pytest.raises(type(error)):
        retrieval.retrieve(profile_id='profile', user_message='hello', recent_messages=[], retrieval_limit=5)
    vector.list_profile_memories.assert_not_called()


def test_embedding_failure_still_allows_current_lexical_fallback():
    vector = Mock()
    vector.search.side_effect = RuntimeError('embedding unavailable')
    vector.list_profile_memories.return_value = []
    assert MemoryChatRetrievalService(service_factory=lambda: vector).retrieve(
        profile_id='profile', user_message='hello', recent_messages=[], retrieval_limit=5) == []
    vector.list_profile_memories.assert_called_once()
    vector.require_evidence_version.assert_called_once()


def test_padded_memory_exclusion_removes_storage_and_both_retrieval_paths(database):
    url, (profile, other) = database
    vector = service(url)
    vector.index(snapshot(profile, ' padded '))
    vector.index(snapshot(other, ' padded '))
    vector.update_memory_usage(profile_id=profile, memory_id='padded', included=False, expected_revision=0)
    assert vector.list_profile_memories(profile_id=profile) == []
    assert vector.search(SearchMemoryRequest(profile_id=profile, query='test')).results == []
    assert len(vector.list_profile_memories(profile_id=other)) == 1


def test_confirmed_address_roundtrip_and_conflict_beyond_retrieval_limit(database):
    url, (profile, other) = database
    vector = service(url)
    batch = snapshot(profile, *(f'memory-{i}' for i in range(120)))
    for item in batch.memories:
        item.confirmed_address = 'Honey'
    batch.memories[-1].confirmed_address = 'Darling'
    vector.index(batch)
    foreign = snapshot(other, 'foreign')
    foreign.memories[0].confirmed_address = 'Someone else'
    vector.index(foreign)
    assert vector.confirmed_profile_address(profile) is None
    assert vector.list_profile_memories(profile_id=profile, limit=1)[0].confirmed_address == 'Honey'
    vector.update_memory_usage(profile_id=profile, memory_id='memory-119', included=False, expected_revision=0)
    assert vector.confirmed_profile_address(profile) == 'Honey'
    evidence = MemoryChatRetrievalService(service_factory=lambda: vector).retrieve(
        profile_id=profile, user_message='hello', recent_messages=[], retrieval_limit=1)
    assert evidence.confirmed_address == 'Honey'
    assert len(evidence) == 1


@pytest.mark.parametrize('mutation', ['delete', 'pending', 'erasure', 'revoke'])
def test_confirmed_address_deleted_or_pending_never_returned(database, mutation):
    url, (profile, _) = database
    vector = service(url)
    batch = snapshot(profile, 'address')
    batch.memories[0].confirmed_address = 'Honey'
    vector.index(batch)
    assert vector.confirmed_profile_address(profile) == 'Honey'
    try:
        if mutation == 'delete':
            vector.delete_memory(profile_id=profile, memory_id='address')
        elif mutation == 'pending':
            vector._reserve_index_generation(snapshot(profile, 'address'))
        else:
            with psycopg.connect(url) as db:
                if mutation == 'erasure':
                    db.execute('INSERT INTO digital_human_profile_erasure_requests(request_id,profile_id,idempotency_key) VALUES(%s,%s,%s)', (uuid4(), profile, str(uuid4())))
                else:
                    db.execute('UPDATE profile_purpose_consents SET purposes=%s WHERE profile_id=%s', ([], profile))
        if mutation in {'revoke', 'erasure'}:
            with pytest.raises(HTTPException):
                vector.confirmed_profile_address(profile)
        else:
            assert vector.confirmed_profile_address(profile) is None
    finally:
        with psycopg.connect(url) as db:
            db.execute('DELETE FROM digital_human_profile_erasure_requests WHERE profile_id=%s', (profile,))


def test_external_address_upsert_and_clear_roundtrip(database):
    url, (profile, _) = database
    vector = service(url)
    batch = snapshot(profile, 'interview')
    batch.memories[0].confirmed_address = 'Honey'
    vector.index_external_memories(batch)
    assert vector.confirmed_profile_address(profile) == 'Honey'
    batch.memories[0].confirmed_address = None
    vector.index_external_memories(batch)
    assert vector.confirmed_profile_address(profile) is None


def test_external_upsert_and_rollback_invalidate_prior_evidence(database):
    url, (profile, _) = database
    vector = service(url)
    vector.index(snapshot(profile, 'local'))
    before = vector.evidence_version(profile)
    batch = snapshot(profile, 'interview')
    generation = vector.index_external_memories(batch)
    with pytest.raises(PGVectorStaleIndexError):
        vector.require_evidence_version(profile, before)
    published = vector.evidence_version(profile)
    assert vector.index_external_memories(batch) == generation
    vector.require_evidence_version(profile, published)
    vector.delete_external_memories(profile_id=profile, memory_ids=['interview'], expected_generation=generation)
    with pytest.raises(PGVectorStaleIndexError):
        vector.require_evidence_version(profile, published)
    assert [item.id for item in vector.list_profile_memories(profile_id=profile)] == ['local']


def test_external_address_change_invalidates_evidence_and_old_rollback(database):
    url, (profile, _) = database
    vector = service(url)
    batch = snapshot(profile, 'interview')
    batch.memories[0].confirmed_address = 'Honey'
    old_generation = vector.index_external_memories(batch)
    before = vector.evidence_version(profile)
    batch.memories[0].confirmed_address = 'Darling'
    vector.index_external_memories(batch)
    with pytest.raises(PGVectorStaleIndexError):
        vector.require_evidence_version(profile, before)
    vector.delete_external_memories(profile_id=profile, memory_ids=['interview'], expected_generation=old_generation)
    assert vector.confirmed_profile_address(profile) == 'Darling'


def test_identical_full_snapshot_reuses_embeddings_and_evidence_version(database):
    url, (profile, _) = database
    vector = service(url)
    batch = snapshot(profile, 'memory')
    batch.memories[0].confirmed_address = 'Honey'
    vector.index(batch)
    before = vector.evidence_version(profile)
    vector._embed = Mock(side_effect=AssertionError('Unchanged content must not be embedded'))
    assert vector.index(batch)['count'] == 1
    vector.require_evidence_version(profile, before)
    vector._embed.assert_not_called()


@pytest.mark.parametrize('change', ['address', 'summary', 'pending'])
def test_changed_or_pending_snapshot_is_not_reused(database, change):
    url, (profile, _) = database
    vector = service(url)
    batch = snapshot(profile, 'memory')
    vector.index(batch)
    before = vector.evidence_version(profile)
    if change == 'address':
        batch.memories[0].confirmed_address = 'Honey'
    elif change == 'summary':
        batch.memories[0].summary = 'Changed'
    else:
        vector._reserve_index_generation(batch)
    vector._embed = Mock(return_value=[1.0] + [0.0] * 1535)
    vector.index(batch)
    vector._embed.assert_called_once()
    assert vector.evidence_version(profile) != before


def test_retrieval_uses_profile_wide_address_not_ranked_subset():
    from app.schemas.vector_memory import SearchMemoryResponse, SearchMemoryResult
    vector = Mock()
    vector.search.return_value = SearchMemoryResponse(results=[SearchMemoryResult(
        id='selected', title='Story', summary='Story', type='text', emotional_tags=[],
        confidence_score=1, similarity_score=1, confirmed_address='Honey')])
    vector.confirmed_profile_address.return_value = None
    evidence = MemoryChatRetrievalService(service_factory=lambda: vector).retrieve(
        profile_id='profile', user_message='hello', recent_messages=[], retrieval_limit=1)
    assert evidence[0].confirmed_address == 'Honey'
    assert evidence.confirmed_address is None
    vector.confirmed_profile_address.assert_called_once_with('profile')
