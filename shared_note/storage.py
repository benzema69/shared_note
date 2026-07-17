from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .mime import detect_mime, extension_for_mime

CHUNK_SIZE = 1024 * 1024


class UploadTooLarge(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StoredUpload:
    sha256: str
    size: int
    mime_type: str
    storage_path: str
    absolute_path: Path
    physical_created: bool


class BlobStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.originals = self.root / "originals"
        self.derived = self.root / "derived"
        self.temp = self.root / "temp"
        for directory in (self.originals, self.derived, self.temp):
            directory.mkdir(parents=True, exist_ok=True)

    def _safe_resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("Unsafe storage path")
        return candidate

    def resolve(self, relative_path: str) -> Path:
        return self._safe_resolve(relative_path)

    def ingest(
        self,
        stream: BinaryIO,
        *,
        original_name: str,
        max_bytes: int,
    ) -> StoredUpload:
        hasher = hashlib.sha256()
        size = 0
        fd, temp_name = tempfile.mkstemp(prefix="upload-", suffix=".part", dir=self.temp)
        temp_path = Path(temp_name)

        try:
            with os.fdopen(fd, "wb") as output:
                while True:
                    chunk = stream.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadTooLarge(
                            f"Upload exceeds the {max_bytes // (1024 * 1024)} MiB limit"
                        )
                    hasher.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())

            sha256 = hasher.hexdigest()
            mime_type = detect_mime(temp_path, original_name)
            extension = extension_for_mime(mime_type, original_name)
            relative = Path("originals") / sha256[:2] / f"{sha256}.{extension}"
            destination = self._safe_resolve(relative.as_posix())
            destination.parent.mkdir(parents=True, exist_ok=True)

            physical_created = False
            if destination.exists():
                temp_path.unlink(missing_ok=True)
            else:
                os.replace(temp_path, destination)
                physical_created = True

            return StoredUpload(
                sha256=sha256,
                size=size,
                mime_type=mime_type,
                storage_path=relative.as_posix(),
                absolute_path=destination,
                physical_created=physical_created,
            )
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def preview_relative_path(self, sha256: str) -> str:
        return (Path("derived") / sha256[:2] / f"{sha256}-preview.webp").as_posix()

    def delete_relative(self, relative_path: str | None) -> None:
        if not relative_path:
            return
        try:
            self._safe_resolve(relative_path).unlink(missing_ok=True)
        except (OSError, ValueError):
            # Orphan cleanup is best-effort. A leftover file is safer than
            # deleting a path outside the storage root.
            return
