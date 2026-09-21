"""Read-only recovery of request-owned Tavus faces, without replaying creation.

Contract: https://docs.tavus.io/api-reference/faces/list-faces
"""
import asyncio
import os
import re

import httpx


class TavusTrainingReconciliationError(RuntimeError):
    """The provider listing cannot establish a complete, unique result."""


async def find_training_face(correlation_name: str) -> dict | None:
    """Return validated identity/status, or None only after a complete empty match.

    The caller must retain an ambiguous create intent when this raises. A None
    result is a listing observation, not permission to replay a provider POST.
    """
    if not isinstance(correlation_name, str) or not re.fullmatch(r'stay_face_[0-9a-f]{32}', correlation_name):
        raise TavusTrainingReconciliationError('Training correlation is invalid.')
    key = os.getenv('TAVUS_API_KEY', '').strip()
    if not key:
        raise TavusTrainingReconciliationError('Training reconciliation is unavailable.')
    try:
        async with asyncio.timeout(30):
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                return await _scan(client, key, correlation_name)
    except (httpx.HTTPError, ValueError, TimeoutError):
        raise TavusTrainingReconciliationError('Training reconciliation is unavailable.') from None


async def _scan(client, key: str, correlation_name: str) -> dict | None:
    seen: set[str] = set()
    match = None
    expected_total = None
    for page in range(1, 101):
        response = await client.get('https://tavusapi.com/v2/faces',
            headers={'x-api-key': key}, params={'limit': 100, 'page': page})
        if response.status_code != 200:
            raise TavusTrainingReconciliationError('Training listing is unavailable.')
        payload = response.json()
        if not isinstance(payload, dict):
            raise TavusTrainingReconciliationError('Training listing is invalid.')
        rows, total = payload.get('data'), payload.get('total_count')
        if not isinstance(rows, list) or len(rows) > 100 or type(total) is not int or total < 0:
            raise TavusTrainingReconciliationError('Training listing is invalid.')
        if expected_total is not None and total != expected_total:
            raise TavusTrainingReconciliationError('Training listing changed during reconciliation.')
        expected_total = total
        for item in rows:
            if not isinstance(item, dict):
                raise TavusTrainingReconciliationError('Training listing is invalid.')
            identifier, name, status = item.get('face_id'), item.get('face_name'), item.get('status')
            if (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', identifier)
                    or not isinstance(name, str) or not name.strip()
                    or not isinstance(status, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', status)):
                raise TavusTrainingReconciliationError('Training listing is invalid.')
            if identifier in seen:
                raise TavusTrainingReconciliationError('Training listing pages overlap.')
            seen.add(identifier)
            if name == correlation_name:
                if match is not None:
                    raise TavusTrainingReconciliationError('Training correlation is ambiguous.')
                # Discard unrelated provider fields, URLs and arbitrary response data.
                match = {'face_id': identifier, 'face_name': name, 'status': status}
        if len(seen) > total:
            raise TavusTrainingReconciliationError('Training listing count is inconsistent.')
        if len(seen) == total:
            return match
        if not rows:
            break
    raise TavusTrainingReconciliationError('Training listing is incomplete.')
