# Shared Note V4 — LAN Drop

Shared Note V4 is a local-first clipboard and file drop for devices on the same trusted network.
It keeps the original files, deduplicates physical blobs by SHA-256, mixes text and files in one history, and keeps deleted items in a 30-day trash.

## Features in this foundation

- Shared live text note, compatible with the V3 `/content` and `/save` endpoints
- Multiple-file upload, drag and drop, and clipboard image paste
- Content-based MIME detection with `filetype` and OOXML inspection
- SHA-256 physical deduplication with multiple logical history entries
- Unified SQLite history for text, URLs, commands, code, and files
- Image previews through Pillow
- Optional PDF previews through `pdftoppm`
- Optional video/audio previews through FFmpeg
- Pin, soft delete, restore, and automatic trash cleanup
- One-command entry point: `python -m shared_note`
- One-time, non-destructive migration of V3 `current` and `history` tables

## Install

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e .
python -m shared_note
```

Then open `http://localhost:8765` on the server or `http://SERVER_LAN_IP:8765` on another device.

### Windows quick start

From PowerShell in the repository:

```powershell
py -m pip install -e .
py -m shared_note
```

After installation, `Launch Shared Note.bat` starts the application from the repository root.

### Optional system tools

They are not required for uploads or downloads:

- `ffmpeg` for video thumbnails and audio waveforms
- `pdftoppm` from Poppler for the first page of PDFs

On Debian/Ubuntu:

```bash
sudo apt install ffmpeg poppler-utils
```

## Runtime layout

By default, runtime data is created in the current directory:

```text
shared_note.db
storage/
├── originals/
│   └── ab/ab12...ef.jpg
├── derived/
│   └── ab/ab12...ef-preview.webp
└── temp/
```

The two-character prefix avoids putting an unlimited number of files in one directory. The path remains relative to `storage/` in SQLite.

Choose another data directory with:

```bash
python -m shared_note --data-dir /srv/shared-note
```

## API

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/history` | Active unified history |
| `GET` | `/history?trash=1` | Trash |
| `GET` | `/history?include_deleted=1` | Active and deleted items |
| `GET` | `/files/{uuid}` | Item metadata |
| `GET` | `/download/{uuid}` | Original bytes with the original filename |
| `GET` | `/preview/{uuid}` | Generated WebP preview |
| `POST` | `/upload` | Files or text |
| `DELETE` | `/files/{uuid}` | Soft delete |
| `POST` | `/files/{uuid}/restore` | Restore |
| `POST` | `/files/{uuid}/pin` | Toggle pin |
| `GET` | `/content` | Current shared note text |
| `POST` | `/save` | Save current shared note text |

### Upload files

```bash
curl -F "device_name=Laptop" \
     -F "files=@photo.jpg" \
     -F "files=@document.pdf" \
     http://localhost:8765/upload
```

### Upload text

```bash
curl -H "Content-Type: application/json" \
     -d '{"text":"https://example.com","device_name":"Laptop"}' \
     http://localhost:8765/upload
```

## Important data-model correction

A text item has no physical blob. Therefore `items.blob_sha256` and `items.original_name` are nullable, with a SQL `CHECK` enforcing one of two valid shapes:

- file item: blob and original name present, text absent;
- text-like item: text present, blob and original name absent.

The V4 draft also keeps the V3 `pinned` capability so the upgrade does not silently remove an existing feature.

## Security boundary

This version is intended for a trusted private LAN. It has upload limits, path containment, content-based storage names, and browser cross-origin write protection, but it does **not** include authentication yet.

Do not forward port 8765 on the router and do not expose it directly to the Internet. Authentication or a VPN such as Tailscale belongs in the next hardening milestone.

## Development

```bash
python -m pip install -e '.[dev]'
pytest
```
