#!/usr/bin/env python3
"""
Shared Note — LAN notepad with local history
Pure stdlib + sqlite3. Zero external deps.
"""

import http.server
import socketserver
import socket
import sqlite3
import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

PORT = 8765
DB_PATH = Path(__file__).with_name("shared_note.db")

# ── DB ──────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS current (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                content TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                device_name TEXT DEFAULT '',
                pinned INTEGER DEFAULT 0,
                content_type TEXT DEFAULT 'text'
            );
            CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_history_pinned ON history(pinned);
        """)
        row = conn.execute("SELECT 1 FROM current WHERE id = 1").fetchone()
        if not row:
            now = datetime.utcnow().isoformat()
            conn.execute(
                "INSERT INTO current (id, content, updated_at) VALUES (1, '', ?)",
                (now,)
            )

def detect_type(text: str) -> str:
    t = text.strip()
    if not t:
        return "text"
    if re.match(r"^https?://\S+$", t, re.I):
        return "url"
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?$", t):
        return "ip"
    if (t.startswith("{") and t.endswith("}")) or (t.startswith("[") and t.endswith("]")):
        try:
            json.loads(t)
            return "json"
        except Exception:
            pass
    if re.match(r"^(npm |yarn |pnpm |git |docker |curl |wget |ssh |scp |python |node |cargo |go |make |cd |ls |cat |sudo )", t, re.I):
        return "cmd"
    if "\n" in t and any(x in t for x in ("def ", "function ", "const ", "let ", "import ", "from ", "class ", "public ", "private ")):
        return "code"
    return "text"

def prune_history(conn, max_age_days=7, max_entries=200):
    cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()
    conn.execute(
        "DELETE FROM history WHERE pinned = 0 AND created_at < ?",
        (cutoff,)
    )
    conn.execute("""
        DELETE FROM history
        WHERE id IN (
            SELECT id FROM history
            WHERE pinned = 0
            ORDER BY created_at DESC
            LIMIT -1 OFFSET ?
        )
    """, (max_entries,))

def get_current(conn) -> str:
    row = conn.execute("SELECT content FROM current WHERE id = 1").fetchone()
    return row["content"] if row else ""

def set_current(conn, content: str, device: str = "", private: bool = False):
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE current SET content = ?, updated_at = ? WHERE id = 1",
        (content, now)
    )
    if private:
        return

    last = conn.execute(
        "SELECT content FROM history ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if last and last["content"] == content:
        return

    ctype = detect_type(content)
    conn.execute(
        "INSERT INTO history (content, created_at, device_name, content_type) VALUES (?, ?, ?, ?)",
        (content, now, device[:64], ctype)
    )
    prune_history(conn)

def list_history(conn, limit=100):
    rows = conn.execute(
        """
        SELECT id, content, created_at, device_name, pinned, content_type
        FROM history
        ORDER BY pinned DESC, created_at DESC
        LIMIT ?
        """,
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]

# ── HTTP ────────────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text, code=200):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/content":
            with get_db() as conn:
                self._text(get_current(conn))
            return

        if path == "/history":
            with get_db() as conn:
                self._json(list_history(conn))
            return

        if path == "/" or path.startswith("/?"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))
            return

        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else ""

        if path == "/save":
            device = qs.get("device", [""])[0]
            private = qs.get("private", ["0"])[0] == "1"
            with get_db() as conn:
                set_current(conn, raw, device=device, private=private)
            self._text("ok")
            return

        if path.startswith("/history/") and path.endswith("/restore"):
            try:
                hid = int(path.split("/")[2])
            except Exception:
                self.send_error(400)
                return
            with get_db() as conn:
                row = conn.execute(
                    "SELECT content FROM history WHERE id = ?", (hid,)
                ).fetchone()
                if not row:
                    self.send_error(404)
                    return
                set_current(conn, row["content"], device=qs.get("device", [""])[0])
            self._text("ok")
            return

        if path.startswith("/history/") and path.endswith("/pin"):
            try:
                hid = int(path.split("/")[2])
            except Exception:
                self.send_error(400)
                return
            with get_db() as conn:
                conn.execute(
                    "UPDATE history SET pinned = 1 - pinned WHERE id = ?", (hid,)
                )
            self._text("ok")
            return

        if path.startswith("/history/") and path.endswith("/delete"):
            try:
                hid = int(path.split("/")[2])
            except Exception:
                self.send_error(400)
                return
            with get_db() as conn:
                conn.execute("DELETE FROM history WHERE id = ?", (hid,))
            self._text("ok")
            return

        self.send_error(404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/history/"):
            try:
                hid = int(path.split("/")[2])
            except Exception:
                self.send_error(400)
                return
            with get_db() as conn:
                conn.execute("DELETE FROM history WHERE id = ?", (hid,))
            self._text("ok")
            return
        self.send_error(404)


# ── HTML ────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Shared Note</title>
<style>
  :root {
    --bg: #0c0c0c;
    --surface: #141414;
    --surface2: #1a1a1a;
    --border: #242424;
    --text: #e8e8e8;
    --muted: #666;
    --accent: #3dd68c;
    --danger: #ff5c5c;
    --warn: #f0c14b;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  header {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    gap: 10px;
    z-index: 20;
  }
  .brand {
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.02em;
    display: flex;
    align-items: center;
    gap: 8px;
    user-select: none;
  }
  .brand .dot {
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--muted);
    transition: background .2s;
  }
  .brand .dot.ok { background: var(--accent); }
  .brand .dot.err { background: var(--danger); }
  .brand .dot.sync { background: var(--warn); }

  .actions {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  button {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--muted);
    font-size: 12px;
    padding: 5px 10px;
    border-radius: 6px;
    cursor: pointer;
    transition: all .15s;
    font-family: inherit;
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  button:hover {
    border-color: #444;
    color: var(--text);
  }
  button:active { transform: scale(0.97); }
  button.active {
    border-color: var(--accent);
    color: var(--accent);
  }
  #status {
    font-size: 11px;
    color: var(--muted);
    min-width: 42px;
    text-align: right;
  }
  .icon-btn {
    padding: 5px 8px;
    font-size: 14px;
  }

  main {
    flex: 1;
    position: relative;
    overflow: hidden;
    display: flex;
  }
  #editor-wrap {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  textarea {
    width: 100%;
    height: 100%;
    padding: 18px 16px 40px;
    font-size: 15.5px;
    line-height: 1.55;
    background: var(--bg);
    color: var(--text);
    border: none;
    outline: none;
    resize: none;
    font-family: ui-monospace, "Cascadia Code", "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  }
  textarea::placeholder { color: #3a3a3a; }

  #panel {
    width: 0;
    overflow: hidden;
    background: var(--surface);
    border-left: 1px solid var(--border);
    transition: width .22s ease;
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
  }
  #panel.open { width: min(340px, 92vw); }
  .panel-header {
    padding: 12px 14px;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 13px;
    font-weight: 600;
  }
  .panel-list {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
  }
  .h-item {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
    margin-bottom: 8px;
    cursor: default;
    transition: border-color .15s;
  }
  .h-item:hover { border-color: #333; }
  .h-item.pinned { border-color: #3a5a3a; }
  .h-meta {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 11px;
    color: var(--muted);
    margin-bottom: 6px;
    gap: 6px;
  }
  .h-type {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 4px;
    background: #222;
    color: #999;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .h-type.url { color: #6cb6ff; }
  .h-type.ip { color: #c792ea; }
  .h-type.json { color: #f0c14b; }
  .h-type.cmd { color: #3dd68c; }
  .h-type.code { color: #ff9d6c; }
  .h-preview {
    font-size: 12.5px;
    line-height: 1.4;
    color: #ccc;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 4.2em;
    overflow: hidden;
    font-family: ui-monospace, Menlo, monospace;
  }
  .h-actions {
    display: flex;
    gap: 6px;
    margin-top: 8px;
  }
  .h-actions button {
    font-size: 11px;
    padding: 3px 8px;
  }
  .empty {
    text-align: center;
    color: var(--muted);
    font-size: 13px;
    padding: 40px 20px;
  }

  footer {
    flex-shrink: 0;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 14px;
    background: var(--surface);
    border-top: 1px solid var(--border);
    font-size: 11px;
    color: var(--muted);
  }
  #counter { font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="dot" id="dot"></span>
    Shared Note
  </div>
  <div class="actions">
    <button id="btn-private" title="Mode privé (ne pas historiser)" class="icon-btn">🔒</button>
    <button id="btn-history" title="Historique (Ctrl+Shift+H)" class="icon-btn">🕘</button>
    <button id="btn-copy" title="Copier">Copy</button>
    <button id="btn-clear" title="Effacer">Clear</button>
    <div id="status">…</div>
  </div>
</header>

<main>
  <div id="editor-wrap">
    <textarea id="note" placeholder="Écris ici…&#10;Les deux appareils voient la même chose en quasi-temps réel." autofocus></textarea>
  </div>
  <aside id="panel">
    <div class="panel-header">
      <span>History</span>
      <button id="btn-close-panel" class="icon-btn">✕</button>
    </div>
    <div class="panel-list" id="history-list">
      <div class="empty">Chargement…</div>
    </div>
  </aside>
</main>

<footer>
  <div id="device-label">LAN only</div>
  <div id="counter">0 chars</div>
</footer>

<script>
const ta = document.getElementById('note');
const statusEl = document.getElementById('status');
const dot = document.getElementById('dot');
const counter = document.getElementById('counter');
const panel = document.getElementById('panel');
const historyList = document.getElementById('history-list');
const btnPrivate = document.getElementById('btn-private');
const deviceLabel = document.getElementById('device-label');

let last = '';
let saving = false;
let dirty = false;
let privateMode = false;
let panelOpen = false;

let deviceName = localStorage.getItem('sn_device') || '';
if (!deviceName) {
  deviceName = prompt('Nom de cet appareil (ex: iPhone, Tour, Laptop) :', 'Device') || 'Device';
  localStorage.setItem('sn_device', deviceName);
}
deviceLabel.textContent = deviceName;

function setStatus(text, state) {
  statusEl.textContent = text;
  dot.className = 'dot ' + (state || '');
}

function updateCounter() {
  const t = ta.value;
  const chars = t.length;
  const words = t.trim() === '' ? 0 : t.trim().split(/\s+/).length;
  counter.textContent = chars + ' chars · ' + words + ' words';
}

function fmtTime(iso) {
  const d = new Date(iso + 'Z');
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return 'à l’instant';
  if (diff < 3600) return Math.floor(diff/60) + ' min';
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
  }
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) {
    return 'Hier ' + d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
  }
  return d.toLocaleDateString([], {day:'2-digit', month:'short'}) + ' ' +
         d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
}

async function load() {
  try {
    const r = await fetch('/content?' + Date.now());
    const t = await r.text();
    if (t !== last && !dirty) {
      const pos = ta.selectionStart;
      const atEnd = pos === ta.value.length;
      ta.value = t;
      if (atEnd) {
        ta.selectionStart = ta.selectionEnd = t.length;
      } else {
        ta.selectionStart = ta.selectionEnd = Math.min(pos, t.length);
      }
      last = t;
      updateCounter();
    }
    setStatus('synced', 'ok');
  } catch (e) {
    setStatus('offline', 'err');
  }
}

async function save() {
  if (saving || !dirty) return;
  saving = true;
  setStatus('…', 'sync');
  const t = ta.value;
  try {
    const q = new URLSearchParams({
      device: deviceName,
      private: privateMode ? '1' : '0'
    });
    await fetch('/save?' + q.toString(), { method: 'POST', body: t });
    last = t;
    dirty = false;
    setStatus(privateMode ? 'private' : 'saved', 'ok');
    if (panelOpen && !privateMode) refreshHistory();
  } catch (e) {
    setStatus('error', 'err');
  }
  saving = false;
}

ta.addEventListener('input', () => {
  dirty = true;
  setStatus('typing', 'sync');
  updateCounter();
  clearTimeout(window._t);
  window._t = setTimeout(save, 320);
});

async function refreshHistory() {
  try {
    const r = await fetch('/history');
    const items = await r.json();
    if (!items.length) {
      historyList.innerHTML = '<div class="empty">Aucune version pour l’instant</div>';
      return;
    }
    historyList.innerHTML = items.map(it => {
      const preview = it.content.length > 160 ? it.content.slice(0, 160) + '…' : it.content;
      const pinLabel = it.pinned ? 'Unpin' : 'Pin';
      return `
        <div class="h-item ${it.pinned ? 'pinned' : ''}" data-id="${it.id}">
          <div class="h-meta">
            <span>${fmtTime(it.created_at)}${it.device_name ? ' · ' + it.device_name : ''}</span>
            <span class="h-type ${it.content_type}">${it.content_type}</span>
          </div>
          <div class="h-preview">${escapeHtml(preview) || '<span style="opacity:.4">∅ vide</span>'}</div>
          <div class="h-actions">
            <button data-act="restore">Restore</button>
            <button data-act="copy">Copy</button>
            <button data-act="pin">${pinLabel}</button>
            <button data-act="delete">Del</button>
          </div>
        </div>`;
    }).join('');
  } catch (e) {
    historyList.innerHTML = '<div class="empty">Erreur de chargement</div>';
  }
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

historyList.addEventListener('click', async (e) => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const item = btn.closest('.h-item');
  const id = item.dataset.id;
  const act = btn.dataset.act;

  if (act === 'restore') {
    await fetch(`/history/${id}/restore?device=${encodeURIComponent(deviceName)}`, { method: 'POST' });
    dirty = false;
    await load();
    setStatus('restored', 'ok');
  } else if (act === 'copy') {
    const r = await fetch('/history');
    const items = await r.json();
    const found = items.find(x => String(x.id) === id);
    if (found) {
      await navigator.clipboard.writeText(found.content);
      setStatus('copied', 'ok');
    }
  } else if (act === 'pin') {
    await fetch(`/history/${id}/pin`, { method: 'POST' });
    refreshHistory();
  } else if (act === 'delete') {
    await fetch(`/history/${id}/delete`, { method: 'POST' });
    refreshHistory();
  }
});

function togglePanel() {
  panelOpen = !panelOpen;
  panel.classList.toggle('open', panelOpen);
  document.getElementById('btn-history').classList.toggle('active', panelOpen);
  if (panelOpen) refreshHistory();
}

document.getElementById('btn-history').addEventListener('click', togglePanel);
document.getElementById('btn-close-panel').addEventListener('click', togglePanel);

btnPrivate.addEventListener('click', () => {
  privateMode = !privateMode;
  btnPrivate.classList.toggle('active', privateMode);
  btnPrivate.textContent = privateMode ? '🔓' : '🔒';
  setStatus(privateMode ? 'private on' : 'synced', privateMode ? 'sync' : 'ok');
});

document.getElementById('btn-copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(ta.value);
    setStatus('copied', 'ok');
  } catch {
    ta.select();
    document.execCommand('copy');
  }
});

document.getElementById('btn-clear').addEventListener('click', () => {
  if (!ta.value) return;
  if (confirm('Effacer tout le contenu actuel ?')) {
    ta.value = '';
    dirty = true;
    updateCounter();
    save();
  }
});

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    dirty = true;
    save();
  }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'h') {
    e.preventDefault();
    togglePanel();
  }
});

window.addEventListener('beforeunload', () => {
  if (dirty) {
    const q = new URLSearchParams({ device: deviceName, private: privateMode ? '1' : '0' });
    navigator.sendBeacon('/save?' + q.toString(), ta.value);
  }
});
ta.addEventListener('blur', save);

setInterval(load, 700);
load();
updateCounter();
</script>
</body>
</html>
"""

# ── Main ────────────────────────────────────────────────────────────────────

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    init_db()
    ip = get_local_ip()
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print("=" * 54)
        print("  SHARED NOTE  +  local history")
        print("=" * 54)
        print(f"  PC      →  http://localhost:{PORT}")
        print(f"  Phone   →  http://{ip}:{PORT}")
        print()
        print("  Même WiFi ou hotspot téléphone.")
        print("  Ctrl+C pour arrêter.")
        print("=" * 54)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
