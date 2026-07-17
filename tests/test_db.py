from __future__ import annotations

from pathlib import Path

from shared_note import db


def test_text_item_can_exist_without_blob(tmp_path: Path) -> None:
    database = tmp_path / "shared_note.db"
    db.init_db(database)
    item = db.create_text_item(
        database,
        content="https://example.com",
        content_type="url",
        device_name="Laptop",
    )
    assert item["blob_sha256"] is None
    assert item["content_type"] == "url"
    assert item["text_content"] == "https://example.com"


def test_soft_delete_and_restore_preserve_created_at(tmp_path: Path) -> None:
    database = tmp_path / "shared_note.db"
    db.init_db(database)
    item = db.create_text_item(
        database,
        content="hello",
        content_type="text",
        device_name="Phone",
    )
    deleted = db.soft_delete_item(database, item["uuid"])
    assert deleted is not None and deleted["is_deleted"] is True
    restored = db.restore_item(database, item["uuid"])
    assert restored is not None and restored["is_deleted"] is False
    assert restored["created_at"] == item["created_at"]
    assert restored["restored_at"] is not None
