from __future__ import annotations

import atexit
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, Response, abort, jsonify, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from . import db
from .mime import detect_text_type
from .models import AppSettings
from .preview import generate_preview_job
from .storage import BlobStorage, UploadTooLarge

LOG = logging.getLogger("shared_note")
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _clean_original_name(value: str | None) -> str:
    if not value:
        return "unnamed.bin"
    value = value.replace("\x00", "").replace("\\", "/")
    name = value.rsplit("/", 1)[-1].strip()
    return (name or "unnamed.bin")[:1024]


def _public_item(item: dict) -> dict:
    public = dict(item)
    public.pop("storage_path", None)
    public.pop("preview_path", None)
    if public.get("content_type") == "file":
        public["download_url"] = f"/download/{public['uuid']}"
        public["preview_url"] = (
            f"/preview/{public['uuid']}" if public.get("has_preview") else None
        )
    else:
        public["download_url"] = None
        public["preview_url"] = None
    return public


def create_app(settings: AppSettings | None = None) -> Flask:
    settings = settings or AppSettings(data_dir=Path.cwd())
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    storage = BlobStorage(settings.storage_root)
    db.init_db(settings.db_path)

    app = Flask(
        __name__,
        static_folder="static",
        static_url_path="/static",
    )
    app.config.update(
        MAX_CONTENT_LENGTH=settings.max_upload_bytes,
        JSON_AS_ASCII=False,
        SETTINGS=settings,
        STORAGE=storage,
    )

    executor = ThreadPoolExecutor(
        max_workers=max(settings.preview_workers, 1),
        thread_name_prefix="shared-note-preview",
    )
    atexit.register(lambda: executor.shutdown(wait=False, cancel_futures=True))

    maintenance_lock = threading.Lock()
    maintenance_state = {"last_run": 0.0}

    def run_maintenance(force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - maintenance_state["last_run"] < 3600:
            return
        if not maintenance_lock.acquire(blocking=False):
            return
        try:
            orphan_paths = db.purge_expired_items(
                settings.db_path,
                retention_days=settings.trash_retention_days,
            )
            for orphan in orphan_paths:
                storage.delete_relative(orphan.get("storage_path"))
                storage.delete_relative(orphan.get("preview_path"))
            maintenance_state["last_run"] = now
        except Exception:
            LOG.exception("Trash maintenance failed")
        finally:
            maintenance_lock.release()

    run_maintenance(force=True)

    def schedule_preview(item: dict) -> None:
        if item.get("content_type") != "file" or not item.get("blob_sha256"):
            return
        if not db.mark_preview_pending(settings.db_path, item["blob_sha256"]):
            return
        executor.submit(
            generate_preview_job,
            db_path=settings.db_path,
            storage=storage,
            sha256=item["blob_sha256"],
            source_relative_path=item["storage_path"],
            mime_type=item["mime_type"],
        )

    @app.before_request
    def protect_browser_writes() -> None:
        run_maintenance()
        if request.method not in MUTATING_METHODS:
            return
        origin = request.headers.get("Origin")
        if not origin:
            return
        origin_host = urlparse(origin).netloc
        if origin_host and origin_host != request.host:
            abort(403, description="Cross-origin writes are not allowed")

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' blob: data:; "
            "style-src 'self'; script-src 'self'; connect-src 'self'",
        )
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def handle_too_large(_: RequestEntityTooLarge):
        return jsonify(
            {
                "error": "upload_too_large",
                "message": f"Maximum request size is {settings.max_upload_bytes // (1024 * 1024)} MiB",
            }
        ), 413

    @app.errorhandler(404)
    def handle_not_found(error):
        if request.path == "/" or request.path.startswith("/static/"):
            return error
        return jsonify({"error": "not_found", "message": str(error.description)}), 404

    @app.errorhandler(400)
    def handle_bad_request(error):
        return jsonify({"error": "bad_request", "message": str(error.description)}), 400

    @app.errorhandler(403)
    def handle_forbidden(error):
        return jsonify({"error": "forbidden", "message": str(error.description)}), 403

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "version": "4.1.0"})

    # Backward-compatible live-note endpoints from V3.
    @app.get("/content")
    def get_content():
        note = db.get_current_note(settings.db_path)
        return Response(note["content"], content_type="text/plain; charset=utf-8")

    @app.post("/save")
    def save_content():
        content = request.get_data(cache=False, as_text=True)
        device_name = request.args.get("device", request.headers.get("X-Device-Name", ""))
        private = request.args.get("private", "0") == "1"
        content_type = detect_text_type(content)
        item = db.save_current_note(
            settings.db_path,
            content=content,
            content_type=content_type,
            device_name=device_name,
            private=private,
        )
        return jsonify({"ok": True, "item": _public_item(item) if item else None})

    @app.get("/history")
    def history():
        include_deleted = request.args.get("include_deleted", "0") == "1"
        only_deleted = request.args.get("trash", "0") == "1"
        try:
            limit = int(request.args.get("limit", "100"))
            offset = int(request.args.get("offset", "0"))
        except ValueError:
            abort(400, description="limit and offset must be integers")
        items = db.list_items(
            settings.db_path,
            include_deleted=include_deleted,
            only_deleted=only_deleted,
            limit=limit,
            offset=offset,
        )
        return jsonify([_public_item(item) for item in items])

    @app.get("/files/<item_uuid>")
    def file_metadata(item_uuid: str):
        item = db.get_item(settings.db_path, item_uuid)
        if not item:
            abort(404, description="Item not found")
        return jsonify(_public_item(item))

    @app.get("/download/<item_uuid>")
    def download(item_uuid: str):
        item = db.get_item(settings.db_path, item_uuid)
        if not item or item.get("content_type") != "file":
            abort(404, description="File not found")
        path = storage.resolve(item["storage_path"])
        if not path.is_file():
            abort(404, description="Stored blob is missing")
        return send_file(
            path,
            mimetype=item["mime_type"],
            as_attachment=True,
            download_name=item["original_name"],
            conditional=True,
            max_age=0,
        )

    @app.get("/preview/<item_uuid>")
    def preview(item_uuid: str):
        item = db.get_item(settings.db_path, item_uuid)
        if not item or not item.get("has_preview"):
            abort(404, description="Preview is not available")
        path = storage.resolve(item["preview_path"])
        if not path.is_file():
            abort(404, description="Preview file is missing")
        return send_file(path, mimetype="image/webp", conditional=True, max_age=3600)

    @app.post("/upload")
    def upload():
        created: list[dict] = []
        device_name = request.headers.get("X-Device-Name", "")

        if request.is_json:
            payload = request.get_json(silent=False) or {}
            text = payload.get("text")
            if not isinstance(text, str):
                abort(400, description="JSON upload requires a string field named 'text'")
            device_name = str(payload.get("device_name", device_name))[:64]
            private = bool(payload.get("private", False))
            if private:
                db.save_current_note(
                    settings.db_path,
                    content=text,
                    content_type=detect_text_type(text),
                    device_name=device_name,
                    private=True,
                )
                return jsonify({"items": []}), 201
            item = db.create_text_item(
                settings.db_path,
                content=text,
                content_type=detect_text_type(text),
                device_name=device_name,
            )
            return jsonify({"items": [_public_item(item)]}), 201

        if request.mimetype == "text/plain":
            text = request.get_data(cache=False, as_text=True)
            item = db.create_text_item(
                settings.db_path,
                content=text,
                content_type=detect_text_type(text),
                device_name=device_name,
            )
            return jsonify({"items": [_public_item(item)]}), 201

        device_name = request.form.get("device_name", device_name)[:64]
        text = request.form.get("text")
        if text is not None:
            item = db.create_text_item(
                settings.db_path,
                content=text,
                content_type=detect_text_type(text),
                device_name=device_name,
            )
            created.append(_public_item(item))

        uploads = request.files.getlist("files") or request.files.getlist("file")
        for incoming in uploads:
            original_name = _clean_original_name(incoming.filename)
            try:
                stored = storage.ingest(
                    incoming.stream,
                    original_name=original_name,
                    max_bytes=settings.max_upload_bytes,
                )
            except UploadTooLarge as exc:
                return jsonify({"error": "upload_too_large", "message": str(exc)}), 413

            try:
                item, blob_created = db.create_file_item(
                    settings.db_path,
                    sha256=stored.sha256,
                    size=stored.size,
                    mime_type=stored.mime_type,
                    storage_path=stored.storage_path,
                    original_name=original_name,
                    device_name=device_name,
                )
            except Exception:
                if stored.physical_created:
                    storage.delete_relative(stored.storage_path)
                raise

            # If the DB already knew this hash but a duplicate physical path was
            # just created, remove the redundant file. Normally the paths match.
            if not blob_created and stored.physical_created:
                known = db.get_item(settings.db_path, item["uuid"])
                if known and known["storage_path"] != stored.storage_path:
                    storage.delete_relative(stored.storage_path)

            schedule_preview(item)
            created.append(_public_item(item))

        if not created:
            abort(400, description="No files or text were supplied")
        return jsonify({"items": created}), 201

    @app.delete("/files/<item_uuid>")
    def delete_item(item_uuid: str):
        item = db.soft_delete_item(settings.db_path, item_uuid)
        if not item:
            abort(404, description="Item not found")
        return jsonify(_public_item(item))

    @app.post("/files/<item_uuid>/restore")
    def restore_item(item_uuid: str):
        item = db.restore_item(settings.db_path, item_uuid)
        if not item:
            abort(404, description="Item not found")
        return jsonify(_public_item(item))

    @app.post("/files/<item_uuid>/pin")
    def pin_item(item_uuid: str):
        item = db.toggle_pin(settings.db_path, item_uuid)
        if not item:
            abort(404, description="Item not found")
        return jsonify(_public_item(item))

    return app
