from __future__ import annotations

import ast
import asyncio
import socket
from pathlib import Path

import pytest

from app.services.avatar_media_storage_service import (
    AvatarMediaStorageService,
)


STORAGE_PATH = Path(
    "app/services/"
    "avatar_media_storage_service.py"
)


def _storage_method_source(
    name: str,
) -> str:
    source = STORAGE_PATH.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(
        source
    )

    storage_class = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.ClassDef,
        )
        and node.name
        == "AvatarMediaStorageService"
    )

    method = next(
        node
        for node in storage_class.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and node.name == name
    )

    lines = source.splitlines(
        keepends=True
    )

    return "".join(
        lines[
            method.lineno - 1:
            method.end_lineno
        ]
    )


def _service(
    tmp_path: Path,
) -> AvatarMediaStorageService:
    return AvatarMediaStorageService(
        storage_root=tmp_path,
        signing_secret=(
            "generated-preview-test-secret"
        ),
        public_base_url=(
            "https://stay.example"
        ),
        environment="test",
    )


def test_secure_ingestion_is_centralized():
    method = _storage_method_source(
        "ingest_remote_generated_preview"
    )

    required = {
        "_validate_remote_media_url(",
        "follow_redirects=False",
        "maximum_redirects = 3",
        "self.max_file_size_bytes",
        "_validate_mp4_signature(",
        "hashlib.sha256()",
        'asset_type=(',
        '"generated_preview"',
        '"video/mp4"',
        "await self.upload(",
    }

    for token in required:
        assert token in method


def test_secure_ingestion_rejects_private_ip(
    tmp_path: Path,
):
    service = _service(
        tmp_path
    )

    with pytest.raises(
        RuntimeError,
        match="non-public",
    ):
        asyncio.run(
            service._validate_remote_media_url(
                "https://127.0.0.1/video.mp4"
            )
        )


def test_secure_ingestion_rejects_http(
    tmp_path: Path,
):
    service = _service(
        tmp_path
    )

    with pytest.raises(
        RuntimeError,
        match="HTTPS",
    ):
        asyncio.run(
            service._validate_remote_media_url(
                "http://example.com/video.mp4"
            )
        )


def test_secure_ingestion_rejects_private_dns_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = _service(
        tmp_path
    )

    def fake_getaddrinfo(
        host,
        port,
        *,
        type,
    ):
        del host
        del port
        del type

        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (
                    "10.0.0.8",
                    443,
                ),
            )
        ]

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        fake_getaddrinfo,
    )

    with pytest.raises(
        RuntimeError,
        match="non-public",
    ):
        asyncio.run(
            service._validate_remote_media_url(
                "https://media.example/video.mp4"
            )
        )


def test_mp4_signature_accepts_iso_base_media():
    AvatarMediaStorageService._validate_mp4_signature(
        b"\x00\x00\x00\x18"
        b"ftyp"
        b"isom"
        b"\x00\x00\x02\x00"
    )


def test_mp4_signature_rejects_non_mp4():
    with pytest.raises(
        RuntimeError,
        match="valid MP4 signature",
    ):
        AvatarMediaStorageService._validate_mp4_signature(
            b"not-an-mp4-file"
        )


def test_remote_url_validator_requires_public_dns():
    method = _storage_method_source(
        "_validate_remote_media_url"
    )

    required = {
        'parsed.scheme.lower() != "https"',
        "socket.getaddrinfo",
        "ipaddress.ip_address",
        "address.is_global",
        "parsed.username is not None",
        "parsed.password is not None",
    }

    for token in required:
        assert token in method
