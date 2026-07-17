from __future__ import annotations

import argparse
import os
from pathlib import Path

from .models import AppSettings
from .server import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shared-note",
        description="Shared Note V4 — local LAN clipboard and file drop",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("SHARED_NOTE_HOST", "0.0.0.0"),
        help="Address to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("SHARED_NOTE_PORT", "8765")),
        help="TCP port (default: 8765)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.getenv("SHARED_NOTE_DATA_DIR", ".")),
        help="Directory containing shared_note.db and storage/",
    )
    parser.add_argument(
        "--max-upload-mb",
        type=int,
        default=int(os.getenv("SHARED_NOTE_MAX_UPLOAD_MB", "512")),
        help="Maximum request size in MiB (default: 512)",
    )
    parser.add_argument(
        "--trash-days",
        type=int,
        default=int(os.getenv("SHARED_NOTE_TRASH_DAYS", "30")),
        help="Trash retention in days (default: 30)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable Flask debug mode (do not use on an untrusted network)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = AppSettings(
        data_dir=args.data_dir.resolve(),
        host=args.host,
        port=args.port,
        max_upload_bytes=max(1, args.max_upload_mb) * 1024 * 1024,
        trash_retention_days=max(1, args.trash_days),
    )
    app = create_app(settings)

    print("Shared Note V4")
    print(f"Data: {settings.data_dir}")
    print(f"Open: http://localhost:{settings.port}")
    print("LAN only: do not expose this port directly to the Internet.")

    app.run(
        host=settings.host,
        port=settings.port,
        debug=args.debug,
        threaded=True,
        use_reloader=args.debug,
    )


if __name__ == "__main__":
    main()
