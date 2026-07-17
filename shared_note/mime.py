from __future__ import annotations

import json
import mimetypes
import re
import zipfile
from pathlib import Path

import filetype

SAFE_EXTENSION_RE = re.compile(r"^[a-z0-9]{1,12}$")

MIME_EXTENSION_OVERRIDES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
    "image/tiff": "tiff",
    "image/svg+xml": "svg",
    "application/pdf": "pdf",
    "application/zip": "zip",
    "application/x-7z-compressed": "7z",
    "application/x-rar-compressed": "rar",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/wav": "wav",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "text/plain": "txt",
}

COMMAND_PREFIXES = (
    "npm ", "yarn ", "pnpm ", "git ", "docker ", "curl ", "wget ",
    "ssh ", "scp ", "python ", "python3 ", "node ", "cargo ", "go ",
    "make ", "cd ", "ls", "cat ", "sudo ", "apt ", "dnf ", "pacman ",
)


def _detect_openxml(path: Path) -> str | None:
    try:
        if not zipfile.is_zipfile(path):
            return None
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "word/document.xml" in names:
                return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if "xl/workbook.xml" in names:
                return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if "ppt/presentation.xml" in names:
                return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            if "META-INF/container.xml" in names:
                return "application/epub+zip"
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def detect_mime(path: Path, original_name: str = "") -> str:
    """Detect MIME from file content, with conservative fallbacks."""
    openxml = _detect_openxml(path)
    if openxml:
        return openxml

    try:
        kind = filetype.guess(path)
    except OSError:
        kind = None
    if kind:
        return kind.mime

    try:
        head = path.read_bytes()[:4096]
    except OSError:
        head = b""

    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        return "application/zip"
    if head.startswith(b"{\\rtf"):
        return "application/rtf"

    guessed, _ = mimetypes.guess_type(original_name, strict=False)
    if guessed:
        return guessed

    if head:
        try:
            head.decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            pass
    return "application/octet-stream"


def extension_for_mime(mime_type: str, original_name: str = "") -> str:
    override = MIME_EXTENSION_OVERRIDES.get(mime_type.lower())
    if override:
        return override

    guessed = mimetypes.guess_extension(mime_type, strict=False)
    if guessed:
        ext = guessed.lstrip(".").lower()
        if SAFE_EXTENSION_RE.fullmatch(ext):
            return ext

    original_ext = Path(original_name).suffix.lstrip(".").lower()
    if SAFE_EXTENSION_RE.fullmatch(original_ext):
        return original_ext
    return "bin"


def detect_text_type(text: str) -> str:
    value = text.strip()
    if not value:
        return "text"
    if re.fullmatch(r"https?://\S+", value, re.IGNORECASE):
        return "url"
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?", value):
        return "ip"
    if (value.startswith("{") and value.endswith("}")) or (
        value.startswith("[") and value.endswith("]")
    ):
        try:
            json.loads(value)
            return "json"
        except json.JSONDecodeError:
            pass
    if value.lower().startswith(COMMAND_PREFIXES):
        return "cmd"
    if "\n" in value and any(
        token in value
        for token in (
            "def ", "class ", "function ", "const ", "let ", "import ",
            "from ", "public ", "private ", "#include ", "SELECT ",
        )
    ):
        return "code"
    return "text"
