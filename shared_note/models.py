from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Runtime configuration for Shared Note."""

    data_dir: Path
    host: str = "0.0.0.0"
    port: int = 8765
    max_upload_bytes: int = 512 * 1024 * 1024
    trash_retention_days: int = 30
    preview_workers: int = 2

    @property
    def db_path(self) -> Path:
        return self.data_dir / "shared_note.db"

    @property
    def storage_root(self) -> Path:
        return self.data_dir / "storage"
