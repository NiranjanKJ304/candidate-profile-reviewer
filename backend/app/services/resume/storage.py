"""Local file storage for original resume files.

Files are named by the resume UUID plus the validated extension - never by the
user-supplied filename - so path traversal through names is impossible. Writes
are atomic (temp file + rename). The storage directory must not be web-served.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from app.core.enums import ResumeErrorCode
from app.services.resume.errors import ProcessingError

_SAFE_EXTENSION = re.compile(r"^[a-z0-9]{1,8}$")


class StorageError(ProcessingError):
    def __init__(self, message: str) -> None:
        super().__init__(ResumeErrorCode.STORAGE_ERROR, message)


class ResumeStorage:
    """UUID-keyed document store. Also used for job descriptions (with ``txt`` allowed)."""

    def __init__(self, root: Path, extensions: tuple[str, ...] = ("pdf", "docx")) -> None:
        self.root = root.resolve()
        if not extensions or not all(_SAFE_EXTENSION.fullmatch(e) for e in extensions):
            raise ValueError("extensions must be short lowercase alphanumerics")
        self._key_pattern = re.compile(rf"^[0-9a-f]{{32}}\.({'|'.join(extensions)})$")

    def ensure_ready(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key_for(resume_id: uuid.UUID, extension: str) -> str:
        return f"{resume_id.hex}{extension}"

    def _path(self, key: str) -> Path:
        if not self._key_pattern.fullmatch(key):
            raise StorageError("Invalid storage key")
        path = (self.root / key).resolve()
        if path.parent != self.root:
            raise StorageError("Invalid storage key")
        return path

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            self.ensure_ready()
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise StorageError("Could not store the uploaded file") from exc

    def read(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("The stored resume file is missing") from exc
        except OSError as exc:
            raise StorageError("Could not read the stored resume file") from exc

    def delete(self, key: str) -> bool:
        try:
            self._path(key).unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageError("Could not delete the stored resume file") from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()
