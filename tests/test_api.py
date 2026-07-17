from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from shared_note.models import AppSettings
from shared_note.server import create_app


def make_png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(output, "PNG")
    return output.getvalue()


def test_upload_deduplicates_blob_but_keeps_two_items(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_dir=tmp_path, preview_workers=1))
    app.config["TESTING"] = True
    client = app.test_client()
    payload = make_png()

    first = client.post(
        "/upload",
        data={"device_name": "Laptop", "files": (io.BytesIO(payload), "one.png")},
        content_type="multipart/form-data",
    )
    second = client.post(
        "/upload",
        data={"device_name": "Phone", "files": (io.BytesIO(payload), "two.png")},
        content_type="multipart/form-data",
    )

    assert first.status_code == 201
    assert second.status_code == 201
    history = client.get("/history").get_json()
    assert len(history) == 2
    assert history[0]["blob_sha256"] == history[1]["blob_sha256"]
    originals = list((tmp_path / "storage" / "originals").rglob("*.png"))
    assert len(originals) == 1


def test_delete_and_restore_routes(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_dir=tmp_path, preview_workers=1))
    app.config["TESTING"] = True
    client = app.test_client()
    created = client.post(
        "/upload",
        json={"text": "git status", "device_name": "Laptop"},
    ).get_json()["items"][0]

    assert client.delete(f"/files/{created['uuid']}").status_code == 200
    assert client.get("/history").get_json() == []
    assert len(client.get("/history?trash=1").get_json()) == 1
    assert client.post(f"/files/{created['uuid']}/restore").status_code == 200
    assert len(client.get("/history").get_json()) == 1
