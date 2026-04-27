"""Storage abstraction для encrypted DNA blobs (ADR-0020 §«Storage»).

Phase 6.2 поставляет одну реализацию — `LocalFilesystemStorage` с
файлами в `DNA_SERVICE_STORAGE_ROOT`. Phase 6.x добавит `S3Storage`
с тем же `Storage` Protocol (drop-in replacement).

Privacy:
    - Каждый blob — отдельный файл с UUID-именем; metadata живёт в БД,
      не в имени файла.
    - delete() — overwrite перед unlink (Phase 6.2.x усилит до DoD-style
      multi-pass overwrite).
    - Нет логов с storage_path в DEBUG/INFO — только в ERROR с context.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Final, Protocol

import anyio

_LOG: Final = logging.getLogger(__name__)


class Storage(Protocol):
    """Async-интерфейс хранения encrypted blob'ов."""

    async def write(self, path: str, data: bytes) -> None:
        """Записать blob по относительному пути. Создаёт parent dirs."""

    async def read(self, path: str) -> bytes:
        """Прочитать blob; raises FileNotFoundError если нет."""

    async def delete(self, path: str) -> None:
        """Удалить blob (overwrite + unlink). Idempotent — no-op если нет."""

    async def exists(self, path: str) -> bool:
        """True если blob существует."""

    def generate_path(self) -> str:
        """Сгенерировать новый уникальный относительный путь для upload."""


class LocalFilesystemStorage:
    """Файловое хранилище blob'ов под одним root-каталогом.

    Поддерживает только относительные пути; абсолютные / `..` traversal
    отвергаются для защиты от directory traversal через user input.
    """

    def __init__(self, root: Path) -> None:
        self._root: Final = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _full_path(self, rel_path: str) -> Path:
        if not rel_path:
            msg = "empty storage path"
            raise ValueError(msg)
        candidate = (self._root / rel_path).resolve()
        # Защита от path traversal: candidate должен быть внутри root.
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            msg = "storage path escapes root"
            raise ValueError(msg) from exc
        return candidate

    async def write(self, path: str, data: bytes) -> None:
        full = self._full_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        async with await anyio.open_file(full, "wb") as fh:
            await fh.write(data)

    async def read(self, path: str) -> bytes:
        full = self._full_path(path)
        async with await anyio.open_file(full, "rb") as fh:
            data: bytes = await fh.read()
        return data

    async def delete(self, path: str) -> None:
        full = self._full_path(path)
        if not full.exists():
            return  # idempotent
        # Best-effort overwrite перед unlink. Phase 6.2.x — multi-pass.
        try:
            size = full.stat().st_size
            async with await anyio.open_file(full, "wb") as fh:
                await fh.write(b"\x00" * size)
        except OSError:
            _LOG.warning("storage overwrite-on-delete failed", exc_info=True)
        try:
            full.unlink()
        except FileNotFoundError:
            return

    async def exists(self, path: str) -> bool:
        return self._full_path(path).is_file()

    def generate_path(self) -> str:
        """Возвращает relative path вида `dna/<uuid>.bin`."""
        return f"dna/{uuid.uuid4()}.bin"
