from __future__ import annotations

import sqlite3
import uuid as uuidlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

ALLOWED_CONTENT_TYPES = {"file", "text", "url", "ip", "json", "cmd", "code"}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blobs (
    sha256          TEXT PRIMARY KEY,
    size            INTEGER NOT NULL CHECK (size >= 0),
    mime_type       TEXT NOT NULL,
    storage_path    TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL,
    preview_status  TEXT NOT NULL DEFAULT 'none'
                    CHECK (preview_status IN ('none', 'pending', 'ready', 'failed')),
    preview_path    TEXT
);

CREATE TABLE IF NOT EXISTS items (
    uuid            TEXT PRIMARY KEY,
    blob_sha256     TEXT REFERENCES blobs(sha256) ON DELETE RESTRICT,
    original_name   TEXT,
    device_name     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    deleted_at      TEXT,
    restored_at     TEXT,
    content_type    TEXT NOT NULL DEFAULT 'file'
                    CHECK (content_type IN ('file', 'text', 'url', 'ip', 'json', 'cmd', 'code')),
    text_content    TEXT,
    pinned          INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    CHECK (
        (content_type = 'file'
            AND blob_sha256 IS NOT NULL
            AND original_name IS NOT NULL
            AND text_content IS NULL)
        OR
        (content_type <> 'file'
            AND blob_sha256 IS NULL
            AND original_name IS NULL
            AND text_content IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_deleted ON items(deleted_at);
CREATE INDEX IF NOT EXISTS idx_items_blob ON items(blob_sha256);
CREATE INDEX IF NOT EXISTS idx_items_pinned ON items(pinned DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS conversions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_sha256   TEXT NOT NULL REFERENCES blobs(sha256) ON DELETE CASCADE,
    target_mime     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'done', 'failed')),
    derived_path    TEXT,
    error_message   TEXT,
    created_at      TEXT NOT NULL,
    finished_at     TEXT,
    UNIQUE (source_sha256, target_mime)
);

CREATE INDEX IF NOT EXISTS idx_conversions_source ON conversions(source_sha256);
CREATE INDEX IF NOT EXISTS idx_conversions_status ON conversions(status);

CREATE TABLE IF NOT EXISTS current_note (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    content         TEXT NOT NULL DEFAULT '',
    content_type    TEXT NOT NULL DEFAULT 'text',
    device_name     TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', '4')"
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO current_note
                (id, content, content_type, device_name, updated_at)
            VALUES (1, '', 'text', '', ?)
            """,
            (utc_now(),),
        )
        _migrate_v3_if_needed(conn)
        conn.commit()


def _migrate_v3_if_needed(conn: sqlite3.Connection) -> None:
    marker = conn.execute(
        "SELECT value FROM schema_meta WHERE key='legacy_v3_migrated'"
    ).fetchone()
    if marker:
        return

    # V3 used tables named `current` and `history`. We leave them intact and
    # copy their data once, so rollback remains possible.
    if _table_exists(conn, "history"):
        columns = _column_names(conn, "history")
        required = {"content", "created_at"}
        if required.issubset(columns):
            select_columns = ["content", "created_at"]
            for optional in ("device_name", "pinned", "content_type"):
                if optional in columns:
                    select_columns.append(optional)

            rows = conn.execute(
                f"SELECT {', '.join(select_columns)} FROM history ORDER BY created_at ASC"
            ).fetchall()
            existing = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
            if existing == 0:
                for row in rows:
                    content = row["content"] or ""
                    ctype = row["content_type"] if "content_type" in row.keys() else "text"
                    if ctype not in ALLOWED_CONTENT_TYPES or ctype == "file":
                        ctype = "text"
                    conn.execute(
                        """
                        INSERT INTO items (
                            uuid, blob_sha256, original_name, device_name,
                            created_at, content_type, text_content, pinned
                        ) VALUES (?, NULL, NULL, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuidlib.uuid4()),
                            row["device_name"] if "device_name" in row.keys() else "",
                            row["created_at"],
                            ctype,
                            content,
                            int(row["pinned"]) if "pinned" in row.keys() else 0,
                        ),
                    )

    if _table_exists(conn, "current"):
        columns = _column_names(conn, "current")
        if {"content", "updated_at"}.issubset(columns):
            row = conn.execute(
                "SELECT content, updated_at FROM current WHERE id = 1"
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE current_note
                    SET content=?, updated_at=?
                    WHERE id=1
                    """,
                    (row["content"] or "", row["updated_at"]),
                )

    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('legacy_v3_migrated', ?) ",
        (utc_now(),),
    )


def serialize_item(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["pinned"] = bool(item.get("pinned", 0))
    item["is_deleted"] = item.get("deleted_at") is not None
    item["has_preview"] = item.get("preview_status") == "ready" and bool(
        item.get("preview_path")
    )
    return item


def get_current_note(db_path: Path) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT content, content_type, device_name, updated_at FROM current_note WHERE id=1"
        ).fetchone()
    return dict(row) if row else {
        "content": "",
        "content_type": "text",
        "device_name": "",
        "updated_at": utc_now(),
    }


def save_current_note(
    db_path: Path,
    *,
    content: str,
    content_type: str,
    device_name: str,
    private: bool,
) -> dict[str, Any] | None:
    if content_type not in ALLOWED_CONTENT_TYPES or content_type == "file":
        content_type = "text"
    now = utc_now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            UPDATE current_note
            SET content=?, content_type=?, device_name=?, updated_at=?
            WHERE id=1
            """,
            (content, content_type, device_name[:64], now),
        )
        if private:
            return None

        last = conn.execute(
            """
            SELECT text_content FROM items
            WHERE content_type <> 'file' AND deleted_at IS NULL
            ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()
        if last and last["text_content"] == content:
            return None

        item_uuid = str(uuidlib.uuid4())
        conn.execute(
            """
            INSERT INTO items (
                uuid, blob_sha256, original_name, device_name,
                created_at, content_type, text_content
            ) VALUES (?, NULL, NULL, ?, ?, ?, ?)
            """,
            (item_uuid, device_name[:64], now, content_type, content),
        )
        row = _get_item_row(conn, item_uuid)
        return serialize_item(row)


def create_text_item(
    db_path: Path,
    *,
    content: str,
    content_type: str,
    device_name: str,
) -> dict[str, Any]:
    if content_type not in ALLOWED_CONTENT_TYPES or content_type == "file":
        content_type = "text"
    item_uuid = str(uuidlib.uuid4())
    now = utc_now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO items (
                uuid, blob_sha256, original_name, device_name,
                created_at, content_type, text_content
            ) VALUES (?, NULL, NULL, ?, ?, ?, ?)
            """,
            (item_uuid, device_name[:64], now, content_type, content),
        )
        row = _get_item_row(conn, item_uuid)
    return serialize_item(row)


def create_file_item(
    db_path: Path,
    *,
    sha256: str,
    size: int,
    mime_type: str,
    storage_path: str,
    original_name: str,
    device_name: str,
) -> tuple[dict[str, Any], bool]:
    """Create a logical file item and insert the physical blob if needed.

    Returns ``(item, blob_was_created)``.
    """
    item_uuid = str(uuidlib.uuid4())
    now = utc_now()
    with transaction(db_path) as conn:
        existing = conn.execute(
            "SELECT sha256 FROM blobs WHERE sha256=?", (sha256,)
        ).fetchone()
        blob_created = existing is None
        if blob_created:
            conn.execute(
                """
                INSERT INTO blobs (
                    sha256, size, mime_type, storage_path, created_at,
                    preview_status, preview_path
                ) VALUES (?, ?, ?, ?, ?, 'none', NULL)
                """,
                (sha256, size, mime_type, storage_path, now),
            )
        else:
            stored = conn.execute(
                "SELECT size, mime_type, storage_path FROM blobs WHERE sha256=?",
                (sha256,),
            ).fetchone()
            if stored["size"] != size:
                raise ValueError("SHA-256 collision or inconsistent blob size")

        conn.execute(
            """
            INSERT INTO items (
                uuid, blob_sha256, original_name, device_name,
                created_at, content_type, text_content
            ) VALUES (?, ?, ?, ?, ?, 'file', NULL)
            """,
            (item_uuid, sha256, original_name[:1024], device_name[:64], now),
        )
        row = _get_item_row(conn, item_uuid)
    return serialize_item(row), blob_created


def _get_item_row(conn: sqlite3.Connection, item_uuid: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            i.uuid, i.blob_sha256, i.original_name, i.device_name,
            i.created_at, i.deleted_at, i.restored_at, i.content_type,
            i.text_content, i.pinned,
            b.size, b.mime_type, b.storage_path,
            b.preview_status, b.preview_path
        FROM items AS i
        LEFT JOIN blobs AS b ON b.sha256 = i.blob_sha256
        WHERE i.uuid = ?
        """,
        (item_uuid,),
    ).fetchone()


def get_item(db_path: Path, item_uuid: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = _get_item_row(conn, item_uuid)
    return serialize_item(row) if row else None


def list_items(
    db_path: Path,
    *,
    include_deleted: bool = False,
    only_deleted: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    where = ""
    if only_deleted:
        where = "WHERE i.deleted_at IS NOT NULL"
    elif not include_deleted:
        where = "WHERE i.deleted_at IS NULL"

    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT
                i.uuid, i.blob_sha256, i.original_name, i.device_name,
                i.created_at, i.deleted_at, i.restored_at, i.content_type,
                i.text_content, i.pinned,
                b.size, b.mime_type, b.storage_path,
                b.preview_status, b.preview_path
            FROM items AS i
            LEFT JOIN blobs AS b ON b.sha256 = i.blob_sha256
            {where}
            ORDER BY i.pinned DESC, i.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return [serialize_item(row) for row in rows]


def soft_delete_item(db_path: Path, item_uuid: str) -> dict[str, Any] | None:
    with transaction(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM items WHERE uuid=?", (item_uuid,)
        ).fetchone()
        if not exists:
            return None
        conn.execute(
            "UPDATE items SET deleted_at=COALESCE(deleted_at, ?) WHERE uuid=?",
            (utc_now(), item_uuid),
        )
        row = _get_item_row(conn, item_uuid)
    return serialize_item(row)


def restore_item(db_path: Path, item_uuid: str) -> dict[str, Any] | None:
    with transaction(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM items WHERE uuid=?", (item_uuid,)
        ).fetchone()
        if not exists:
            return None
        now = utc_now()
        conn.execute(
            "UPDATE items SET deleted_at=NULL, restored_at=? WHERE uuid=?",
            (now, item_uuid),
        )
        row = _get_item_row(conn, item_uuid)
    return serialize_item(row)


def toggle_pin(db_path: Path, item_uuid: str) -> dict[str, Any] | None:
    with transaction(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM items WHERE uuid=?", (item_uuid,)
        ).fetchone()
        if not exists:
            return None
        conn.execute(
            "UPDATE items SET pinned=1-pinned WHERE uuid=?", (item_uuid,)
        )
        row = _get_item_row(conn, item_uuid)
    return serialize_item(row)


def mark_preview_pending(db_path: Path, sha256: str) -> bool:
    with transaction(db_path) as conn:
        row = conn.execute(
            "SELECT preview_status FROM blobs WHERE sha256=?", (sha256,)
        ).fetchone()
        if not row or row["preview_status"] in {"pending", "ready"}:
            return False
        conn.execute(
            "UPDATE blobs SET preview_status='pending' WHERE sha256=?", (sha256,)
        )
    return True


def set_preview_result(
    db_path: Path,
    *,
    sha256: str,
    status: str,
    preview_path: str | None,
) -> None:
    if status not in {"none", "pending", "ready", "failed"}:
        raise ValueError("Invalid preview status")
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE blobs SET preview_status=?, preview_path=? WHERE sha256=?",
            (status, preview_path, sha256),
        )


def purge_expired_items(
    db_path: Path, *, retention_days: int
) -> list[dict[str, str]]:
    """Delete expired logical items and orphaned blob rows.

    Returns physical paths that may be removed after the transaction commits.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max(retention_days, 1))
    ).isoformat(timespec="seconds")

    with transaction(db_path) as conn:
        conn.execute(
            "DELETE FROM items WHERE deleted_at IS NOT NULL AND deleted_at < ?",
            (cutoff,),
        )
        orphan_rows = conn.execute(
            """
            SELECT b.sha256, b.storage_path, b.preview_path
            FROM blobs AS b
            LEFT JOIN items AS i ON i.blob_sha256 = b.sha256
            WHERE i.uuid IS NULL
            """
        ).fetchall()
        if orphan_rows:
            conn.executemany(
                "DELETE FROM blobs WHERE sha256=?",
                [(row["sha256"],) for row in orphan_rows],
            )
    return [dict(row) for row in orphan_rows]
