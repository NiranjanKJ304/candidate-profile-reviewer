"""Resume file storage: atomic writes, key validation (path traversal), failures."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.enums import ResumeErrorCode
from app.services.resume.storage import ResumeStorage, StorageError


@pytest.fixture
def storage(tmp_path: Path) -> ResumeStorage:
    return ResumeStorage(tmp_path / "resumes")


def test_round_trip_and_delete(storage: ResumeStorage) -> None:
    key = storage.key_for(uuid.uuid4(), ".pdf")
    storage.save(key, b"%PDF-data")
    assert storage.exists(key)
    assert storage.read(key) == b"%PDF-data"
    assert storage.delete(key) is True
    assert storage.exists(key) is False
    assert storage.delete(key) is False  # idempotent


def test_key_is_uuid_hex_plus_extension(storage: ResumeStorage) -> None:
    rid = uuid.uuid4()
    assert storage.key_for(rid, ".docx") == f"{rid.hex}.docx"


def test_save_creates_directory_and_leaves_no_temp_files(storage: ResumeStorage) -> None:
    key = storage.key_for(uuid.uuid4(), ".pdf")
    storage.save(key, b"x")
    assert [p.name for p in storage.root.iterdir()] == [key]


@pytest.mark.parametrize(
    "key",
    [
        "../../etc/passwd",
        "..\\..\\windows\\win.ini",
        "/absolute/path.pdf",
        "C:\\temp\\x.pdf",
        "resume.pdf",
        f"{uuid.uuid4().hex}.exe",
        f"{uuid.uuid4().hex}.pdf/../../x",
        f"sub/{uuid.uuid4().hex}.pdf",
        "",
    ],
)
def test_unsafe_keys_are_refused(storage: ResumeStorage, key: str) -> None:
    for operation in (storage.read, storage.delete, storage.exists):
        with pytest.raises(StorageError) as info:
            operation(key)
        assert info.value.code is ResumeErrorCode.STORAGE_ERROR
    with pytest.raises(StorageError):
        storage.save(key, b"x")


def test_missing_file(storage: ResumeStorage) -> None:
    with pytest.raises(StorageError, match="missing"):
        storage.read(storage.key_for(uuid.uuid4(), ".pdf"))


def test_write_failure_is_reported_and_cleaned_up(storage: ResumeStorage, tmp_path: Path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where the directory should be")
    bad = ResumeStorage(blocker)
    with pytest.raises(StorageError, match="Could not store"):
        bad.save(bad.key_for(uuid.uuid4(), ".pdf"), b"x")
