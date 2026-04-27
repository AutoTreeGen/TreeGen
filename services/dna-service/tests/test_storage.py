"""Тесты LocalFilesystemStorage — без БД, чистая файловая логика."""

from __future__ import annotations

from pathlib import Path

import pytest
from dna_service.services.storage import LocalFilesystemStorage


@pytest.mark.asyncio
async def test_write_then_read(tmp_path: Path) -> None:
    storage = LocalFilesystemStorage(tmp_path)
    path = storage.generate_path()
    await storage.write(path, b"hello dna")
    assert await storage.exists(path)
    assert await storage.read(path) == b"hello dna"


@pytest.mark.asyncio
async def test_delete_is_idempotent(tmp_path: Path) -> None:
    storage = LocalFilesystemStorage(tmp_path)
    path = storage.generate_path()
    # delete before write — no error.
    await storage.delete(path)
    await storage.write(path, b"data")
    await storage.delete(path)
    assert not await storage.exists(path)
    # Second delete — also no error.
    await storage.delete(path)


@pytest.mark.asyncio
async def test_delete_overwrites_then_unlinks(tmp_path: Path) -> None:
    storage = LocalFilesystemStorage(tmp_path)
    path = storage.generate_path()
    await storage.write(path, b"sensitive")
    await storage.delete(path)
    assert not await storage.exists(path)


@pytest.mark.asyncio
async def test_path_traversal_rejected(tmp_path: Path) -> None:
    storage = LocalFilesystemStorage(tmp_path)
    with pytest.raises(ValueError, match="escapes root"):
        await storage.write("../escape.bin", b"x")


@pytest.mark.asyncio
async def test_empty_path_rejected(tmp_path: Path) -> None:
    storage = LocalFilesystemStorage(tmp_path)
    with pytest.raises(ValueError, match="empty storage path"):
        await storage.write("", b"x")


def test_generate_path_is_unique(tmp_path: Path) -> None:
    storage = LocalFilesystemStorage(tmp_path)
    paths = {storage.generate_path() for _ in range(50)}
    assert len(paths) == 50


def test_root_directory_created(tmp_path: Path) -> None:
    nested = tmp_path / "deeply" / "nested" / "root"
    LocalFilesystemStorage(nested)
    assert nested.is_dir()
