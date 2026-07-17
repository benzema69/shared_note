#!/usr/bin/env python3
"""
Shared Note — ultra simple LAN notepad
PC + phone on same WiFi (or phone hotspot)
Zero deps, pure stdlib.
"""

import http.server
import socketserver
import socket
from pathlib import Path

PORT = 8765
NOTE_FILE = Path(__file__).with_name("shared_note.txt")

if not NOTE_FILE.exists():
    NOTE_FILE.write_text("", encoding="utf-8")

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
    --border: #222;
    --text: #e8e8e8;
    --muted: #666;
    --accent: #3dd68c;
    --danger: #ff5c5c;
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

  /* Header */
  header {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    gap: 12px;
  }
  .brand {
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: var(--text);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .brand span {
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--muted);
    transition: background .25s;
  }
  .brand span.ok { background: var(--accent); }
  .brand span.err { background: var(--danger); }
  .brand span.sync { background: #f0c14b; }

  .actions {
    display: flex;
    align-items: center;
    gap: 8px;
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
  }
  button:hover {
    border-color: #444;
    color: var(--text);
  }
  button:active { transform: scale(0.97); }
  #status {
    font-size: 11px;
    color: var(--muted);
    min-width: 48px;
    text-align: right;
  }

  /* Editor */
  main {
    flex: 1;
    position: relative;
    overflow: hidden;
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

  /* Footer */
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
    <span id="dot"></span>
    Shared Note
  </div>
  <div class="actions">
    <button id="btn-copy" title="Copier tout">Copy</button>
    <button id="btn-clear" title="Tout effacer">Clear</button>
    <div id="status">…</div>
  </div>
</header>

<main>
  <textarea id="note" placeholder="Écris ici…&#10;Les deux appareils voient la même chose en quasi-temps réel." autofocus></textarea>
</main>

<footer>
  <div>LAN only · no cloud</div>
  <div id="counter">0 chars</div>
</footer>

<script>
const ta = document.getElementById('note');
const statusEl = document.getElementById('status');
const dot = document.getElementById('dot');
const counter = document.getElementById('counter');

let last = '';
let saving = false;
let dirty = false;

function setStatus(text, state) {
  statusEl.textContent = text;
  dot.className = state || '';
}

function updateCounter() {
  const t = ta.value;
  const chars = t.length;
  const words = t.trim() === '' ? 0 : t.trim().split(/\s+/).length;
  counter.textContent = chars + ' chars · ' + words + ' words';
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
    await fetch('/save', { method: 'POST', body: t });
    last = t;
    dirty = false;
    setStatus('saved', 'ok');
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
  window._t = setTimeout(save, 280);
});

document.getElementById('btn-copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(ta.value);
    setStatus('copied', 'ok');
    setTimeout(() => setStatus('synced', 'ok'), 1200);
  } catch {
    // fallback
    ta.select();
    document.execCommand('copy');
  }
});

document.getElementById('btn-clear').addEventListener('click', () => {
  if (!ta.value) return;
  if (confirm('Effacer tout le contenu ?')) {
    ta.value = '';
    dirty = true;
    updateCounter();
    save();
  }
});

window.addEventListener('beforeunload', () => {
  if (dirty) navigator.sendBeacon('/save', ta.value);
});
ta.addEventListener('blur', save);

// Keyboard: Ctrl/Cmd+S force save
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    dirty = true;
    save();
  }
});

setInterval(load, 650);
load();
updateCounter();
</script>
</body>
</html>
"""

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/content"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(NOTE_FILE.read_text(encoding="utf-8").encode("utf-8"))
        elif self.path == "/" or self.path.startswith("/?"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/save":
            length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(length).decode("utf-8")
            NOTE_FILE.write_text(data, encoding="utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_error(404)

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
    ip = get_local_ip()
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print("=" * 52)
        print("  SHARED NOTE  —  LAN only")
        print("=" * 52)
        print(f"  PC      →  http://localhost:{PORT}")
        print(f"  Phone   →  http://{ip}:{PORT}")
        print()
        print("  Même WiFi ou hotspot téléphone.")
        print("  Ctrl+C pour arrêter.")
        print("=" * 52)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
