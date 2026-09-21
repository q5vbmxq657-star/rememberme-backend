import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.avatar_media_storage_service import AvatarMediaStorageService


def storage(tmp_path, monkeypatch, cap='12'):
    monkeypatch.setenv('AVATAR_VIDEO_MAX_FILE_SIZE_BYTES', cap)
    monkeypatch.setenv('AVATAR_MEDIA_MAX_FILE_SIZE_BYTES', '5')
    service = AvatarMediaStorageService(storage_root=tmp_path, environment='development')
    service.sign_download_url = lambda **_: SimpleNamespace(signed_url='https://example.test/media')
    return service


@pytest.mark.parametrize('asset_type', ['video', 'training_sample'])
def test_training_video_accepts_exact_cap_in_small_chunks(tmp_path, monkeypatch, asset_type):
    service = storage(tmp_path, monkeypatch)
    upload = SimpleNamespace(filename='test.mp4', content_type='video/mp4',
        read=AsyncMock(side_effect=[b'1234', b'5678', b'9012', b'']))
    result = asyncio.run(service.upload(str(uuid4()), asset_type, 'Test', upload))
    assert result.size_bytes == 12


@pytest.mark.parametrize('asset_type,mime,filename,cap', [
    ('video', 'video/mp4', 'test.mp4', 12),
    ('training_sample', 'video/mp4', 'test.mp4', 12),
    ('memory_video', 'video/mp4', 'test.mp4', 5),
    ('image', 'image/jpeg', 'test.jpg', 5),
    ('memory_image', 'image/jpeg', 'test.jpg', 5),
    ('voice', 'audio/mpeg', 'test.mp3', 5),
])
def test_streaming_overflow_uses_correct_limit_and_leaves_no_file(tmp_path, monkeypatch, asset_type, mime, filename, cap):
    service = storage(tmp_path, monkeypatch)
    upload = SimpleNamespace(filename=filename, content_type=mime,
        read=AsyncMock(side_effect=[b'x' * cap, b'x', b'']))
    with pytest.raises(RuntimeError, match=rf'{cap} bytes'):
        asyncio.run(service.upload(str(uuid4()), asset_type, 'Test', upload))
    assert not [path for path in tmp_path.rglob('*') if path.is_file()]


@pytest.mark.parametrize('cap', ['0', '-1', '750000001', 'invalid'])
def test_invalid_video_limit_is_rejected(tmp_path, monkeypatch, cap):
    with pytest.raises(ValueError):
        storage(tmp_path, monkeypatch, cap)


def test_default_is_750_decimal_mb_without_changing_general_limit(tmp_path, monkeypatch):
    monkeypatch.delenv('AVATAR_VIDEO_MAX_FILE_SIZE_BYTES', raising=False)
    monkeypatch.delenv('AVATAR_MEDIA_MAX_FILE_SIZE_BYTES', raising=False)
    service = AvatarMediaStorageService(storage_root=tmp_path, environment='development')
    assert service.avatar_video_max_file_size_bytes == 750_000_000
    assert service.max_file_size_bytes == 52_428_800
