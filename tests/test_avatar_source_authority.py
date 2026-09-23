import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services.avatar_media_storage_service import AvatarMediaAssetNotFoundError
from app.services.avatar_media_analysis_service import AvatarMediaAnalysisError, AvatarMediaAnalysisUnavailableError
from app.services.avatar_provider_service import AvatarProviderService, AvatarProviderStatusUnavailableError


@pytest.fixture
def authority():
    profile = str(uuid4())
    photo, video = str(uuid4()), str(uuid4())
    records = {
        photo: SimpleNamespace(profile_id=profile, asset_type='image', content_type='image/jpeg'),
        video: SimpleNamespace(profile_id=profile, asset_type='video', content_type='video/mp4'),
    }
    storage = Mock()
    storage.resolve_external_base_url.return_value = 'https://media.stay.example'
    def metadata(asset_id):
        if asset_id not in records:
            raise AvatarMediaAssetNotFoundError('not found')
        return records[asset_id]
    storage.get_metadata.side_effect = metadata
    storage.sign_provider_training_url.side_effect = lambda **kw: SimpleNamespace(
        signed_url=f"https://media.stay.example/fresh/{kw['asset_id']}")
    service = AvatarProviderService()
    service._profile_repository = Mock()
    service._profile_repository.get_avatar_training_request.return_value = None
    service._media_storage_service = storage
    service._validate_training_asset = Mock()
    return service, storage, profile, photo, video, records


def test_asset_authority_precedes_stale_direct_link(authority):
    service, storage, profile, photo, _, _ = authority
    source = service._extract_tavus_training_source({
        'train_image_url': 'https://foreign.example/private-photo',
        'identityPhotos': [{'remoteAssetID': photo, 'remoteURL': 'https://expired.example'}],
    }, profile_id=profile)
    assert source == ('train_image_url', f'https://media.stay.example/fresh/{photo}')
    storage.sign_provider_training_url.assert_called_once_with(asset_id=photo, profile_id=profile)
    service._validate_training_asset.assert_called_once()


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_legacy_owned_url_is_resigned_even_when_expired(authority, kind):
    service, storage, profile, photo, video, _ = authority
    asset = photo if kind == 'image' else video
    source = service._extract_tavus_training_source({
        f'train_{kind}_url': f'https://media.stay.example/v1/avatar-media/public/assets/{asset}?expires=1&signature=old',
    }, profile_id=profile)
    assert source == (f'train_{kind}_url', f'https://media.stay.example/fresh/{asset}')
    storage.sign_provider_training_url.assert_called_once()


@pytest.mark.parametrize('url', [
    'https://foreign.example/v1/avatar-media/public/assets/{asset}',
    'https://media.stay.example.attacker.test/v1/avatar-media/public/assets/{asset}',
    'https://media.stay.example@attacker.test/v1/avatar-media/public/assets/{asset}',
    'http://media.stay.example/v1/avatar-media/public/assets/{asset}',
    'https://media.stay.example/v1/avatar-media/public/assets/{asset}/other',
    'https://media.stay.example/v1/avatar-media/public/assets/%2e%2e',
    'https://media.stay.example/v1/avatar-media/public/assets/{asset}#fragment',
])
def test_untrusted_legacy_url_is_never_disclosed(authority, url):
    service, storage, profile, photo, _, _ = authority
    assert service._extract_tavus_training_source(
        {'train_image_url': url.format(asset=photo)}, profile_id=profile) is None
    storage.sign_provider_training_url.assert_not_called()


@pytest.mark.parametrize('unavailable', [False, True])
def test_old_upload_is_revalidated_before_provider_disclosure(authority, monkeypatch, unavailable):
    service, storage, profile, photo, _, _ = authority
    monkeypatch.setenv('TAVUS_API_KEY', 'test-only')
    error_type = AvatarMediaAnalysisUnavailableError if unavailable else AvatarMediaAnalysisError
    service._validate_training_asset.side_effect = error_type('Video needs 60 seconds.')
    operation = service.submit(provider='tavus', profile_id=profile,
        package_record_id=str(uuid4()), package={'identityPhotos': [{'remoteAssetID': photo}]})
    if unavailable:
        with pytest.raises(AvatarProviderStatusUnavailableError):
            asyncio.run(operation)
    else:
        result = asyncio.run(operation)
        assert result.status == 'failed'
        assert result.error_message == 'Video needs 60 seconds.'
    storage.sign_provider_training_url.assert_not_called()


def test_real_small_legacy_portrait_is_not_allowed_as_training_source(tmp_path):
    import cv2
    import numpy as np
    path = tmp_path / 'small.jpg'
    assert cv2.imwrite(str(path), np.zeros((295, 316, 3), dtype=np.uint8))
    metadata = SimpleNamespace(storage_path=str(path), content_type='image/jpeg')
    with pytest.raises(AvatarMediaAnalysisError, match='512 x 512'):
        AvatarProviderService._validate_training_asset(metadata, 'image')


@pytest.mark.parametrize('mutation', ['foreign', 'gallery', 'generated', 'wrong_type', 'deleted'])
def test_authoritative_metadata_rejects_unsafe_material(authority, mutation):
    service, storage, profile, photo, _, records = authority
    if mutation == 'foreign':
        records[photo].profile_id = str(uuid4())
    elif mutation == 'gallery':
        records[photo].asset_type = 'memory_image'
    elif mutation == 'generated':
        records[photo].asset_type = 'generated_preview'
    elif mutation == 'wrong_type':
        records[photo].content_type = 'video/mp4'
    else:
        del records[photo]
    assert service._extract_tavus_training_source({
        'identityPhotos': [{'remoteAssetID': photo, 'remoteURL': 'https://foreign.example/fallback'}],
    }, profile_id=profile) is None
    storage.sign_provider_training_url.assert_not_called()


def test_selected_missing_asset_never_uses_legacy_replacement(authority):
    service, storage, profile, photo, _, _ = authority
    assert service._extract_tavus_training_source({
        'identityPhotos': [{'remoteAssetID': str(uuid4())}],
        'train_image_url': f'https://media.stay.example/v1/avatar-media/public/assets/{photo}',
    }, profile_id=profile) is None
    storage.sign_provider_training_url.assert_not_called()


def test_invalid_selected_video_does_not_silently_train_photo(authority):
    service, storage, profile, photo, _, _ = authority
    assert service._extract_tavus_training_source({
        'motionVideos': [{'remoteAssetID': str(uuid4())}],
        'identityPhotos': [{'remoteAssetID': photo}],
    }, profile_id=profile) is None
    storage.sign_provider_training_url.assert_not_called()


def test_invalid_generic_video_does_not_silently_train_photo(authority):
    service, storage, profile, photo, _, _ = authority
    assert service._extract_tavus_training_source({
        'assets': [{'type': 'video', 'remoteAssetID': str(uuid4())},
                   {'type': 'image', 'remoteAssetID': photo}],
    }, profile_id=profile) is None
    storage.sign_provider_training_url.assert_not_called()


@pytest.mark.parametrize('mode_key', ['tavus_training_mode', 'tavusTrainingMode'])
def test_explicit_photo_mode_never_accesses_unselected_video(authority, mode_key):
    service, storage, profile, photo, _, _ = authority
    assert service._extract_tavus_training_source({
        mode_key: 'image',
        'motionVideos': [{'remoteAssetID': str(uuid4())}],
        'identityPhotos': [{'remoteAssetID': photo}],
    }, profile_id=profile)[0] == 'train_image_url'
    storage.get_metadata.assert_called_once_with(photo)


@pytest.mark.parametrize('error', [OSError('private storage detail'), ValueError('invalid metadata')])
def test_storage_outage_is_retryable_not_invalid_material(authority, monkeypatch, error):
    service, storage, profile, photo, _, _ = authority
    monkeypatch.setenv('TAVUS_API_KEY', 'test-only')
    storage.get_metadata.side_effect = error
    with pytest.raises(AvatarProviderStatusUnavailableError, match='temporarily unavailable'):
        asyncio.run(service.submit(provider='tavus', profile_id=profile,
            package_record_id=str(uuid4()), package={'identityPhotos': [{'remoteAssetID': photo}]}))
    storage.sign_provider_training_url.assert_not_called()
