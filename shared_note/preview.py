from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from . import db
from .storage import BlobStorage

MAX_PREVIEW_PIXELS = 80_000_000
PREVIEW_SIZE = (1200, 1200)


class PreviewUnavailable(RuntimeError):
    pass


def _save_webp(source: Path, destination: Path) -> None:
    Image.MAX_IMAGE_PIXELS = MAX_PREVIEW_PIXELS
    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(PREVIEW_SIZE)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, format="WEBP", quality=82, method=4)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise PreviewUnavailable(str(exc)) from exc


def _render_pdf(source: Path, temp_dir: Path) -> Path:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise PreviewUnavailable("pdftoppm is not installed")
    output_prefix = temp_dir / "page"
    subprocess.run(
        [pdftoppm, "-f", "1", "-singlefile", "-png", "-r", "120", str(source), str(output_prefix)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=45,
    )
    rendered = output_prefix.with_suffix(".png")
    if not rendered.exists():
        raise PreviewUnavailable("PDF renderer produced no image")
    return rendered


def _render_video(source: Path, temp_dir: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise PreviewUnavailable("ffmpeg is not installed")
    rendered = temp_dir / "frame.png"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "1",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(1200,iw)':-2",
            "-y",
            str(rendered),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )
    if not rendered.exists():
        raise PreviewUnavailable("Video renderer produced no image")
    return rendered


def _render_audio(source: Path, temp_dir: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise PreviewUnavailable("ffmpeg is not installed")
    rendered = temp_dir / "waveform.png"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-filter_complex",
            "aformat=channel_layouts=mono,showwavespic=s=1200x320",
            "-frames:v",
            "1",
            "-y",
            str(rendered),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )
    if not rendered.exists():
        raise PreviewUnavailable("Audio renderer produced no image")
    return rendered


def generate_preview_job(
    *,
    db_path: Path,
    storage: BlobStorage,
    sha256: str,
    source_relative_path: str,
    mime_type: str,
) -> None:
    """Generate one shared preview for a physical blob.

    The function is designed to run in a background executor. Failures are
    recorded in the database and do not affect the uploaded original.
    """
    destination_relative = storage.preview_relative_path(sha256)
    destination = storage.resolve(destination_relative)
    source = storage.resolve(source_relative_path)

    try:
        with tempfile.TemporaryDirectory(dir=storage.temp) as temp_name:
            temp_dir = Path(temp_name)
            render_source = source
            if mime_type == "application/pdf":
                render_source = _render_pdf(source, temp_dir)
            elif mime_type.startswith("video/"):
                render_source = _render_video(source, temp_dir)
            elif mime_type.startswith("audio/"):
                render_source = _render_audio(source, temp_dir)
            elif not mime_type.startswith("image/"):
                raise PreviewUnavailable(f"No preview generator for {mime_type}")

            temp_preview = temp_dir / "preview.webp"
            _save_webp(render_source, temp_preview)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp_preview.replace(destination)

        db.set_preview_result(
            db_path,
            sha256=sha256,
            status="ready",
            preview_path=destination_relative,
        )
    except Exception:
        destination.unlink(missing_ok=True)
        db.set_preview_result(
            db_path,
            sha256=sha256,
            status="failed",
            preview_path=None,
        )
