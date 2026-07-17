from __future__ import annotations

from pathlib import Path

from shared_note.models import AppSettings
from shared_note.server import create_app


def test_v41_shell_is_served(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_dir=tmp_path, preview_workers=1))
    app.config["TESTING"] = True
    response = app.test_client().get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for marker in (
        'id="app-sidebar"',
        'data-section="upload"',
        'id="upload-modal"',
        'id="settings-modal"',
        'id="drag-overlay"',
        'id="history-panel"',
    ):
        assert marker in html
