#!/usr/bin/env python3
"""
Shared Note — ultra simple LAN notepad
PC + phone on same WiFi (or phone hotspot)
Zero deps, pure stdlib.
"""

import http.server
import socketserver
import os
import socket
from pathlib import Path

PORT = 8765
NOTE_FILE = Path(__file__).with_name("shared_note.txt")

if not NOTE_FILE.exists():
    NOTE_FILE.write_text("", encoding="utf-8")

HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Shared Note</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d0d0d; color: #e0e0e0; font-family: system-ui, -apple-system, sans-serif; height: 100vh; overflow: hidden; }
  #status { position: fixed; top: 8px; right: 12px; font-size: 11px; opacity: 0.5; z-index: 10; }
  textarea {
    width: 100%; height: 100%; padding: 48px 16px 16px;
    font-size: 16px; line-height: 1.5;
    background: #0d0d0d; color: #e0e0e0;
    border: none; outline: none; resize: none;
    font-family: ui-monospace, "Cascadia Code", "SF Mono", Menlo, monospace;
  }
  textarea::placeholder { color: #444; }
</style>
</head>
<body>
<div id="status">sync...</div>
<textarea id="note" placeholder="Écris ici. Les deux côtés voient la même chose en quasi-temps réel." autofocus></textarea>
<script>
const ta = document.getElementById('note');
const status = document.getElementById('status');
let last = '';
let saving = false;
let dirty = false;

async function load() {
  try {
    const r = await fetch('/content?' + Date.now());
    const t = await r.text();
    if (t !== last && !dirty) {
      const pos = ta.selectionStart;
      const wasAtEnd = pos === ta.value.length;
      ta.value = t;
      if (wasAtEnd) {
        ta.selectionStart = ta.selectionEnd = t.length;
      } else {
        ta.selectionStart = ta.selectionEnd = Math.min(pos, t.length);
      }
      last = t;
    }
    status.textContent = 'ok';
  } catch (e) {
    status.textContent = 'offline';
  }
}

async function save() {
  if (saving || !dirty) return;
  saving = true;
  const t = ta.value;
  try {
    await fetch('/save', { method: 'POST', body: t });
    last = t;
    dirty = false;
    status.textContent = 'saved';
  } catch (e) {
    status.textContent = 'err';
  }
  saving = false;
}

ta.addEventListener('input', () => {
  dirty = true;
  status.textContent = '…';
  clearTimeout(window._t);
  window._t = setTimeout(save, 250);
});

// Force save on blur / leave
window.addEventListener('beforeunload', () => { if (dirty) navigator.sendBeacon('/save', ta.value); });
ta.addEventListener('blur', save);

setInterval(load, 700);
load();
</script>
</body>
</html>
"""

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # quiet

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
        print("=" * 50)
        print("  SHARED NOTE  —  LAN only")
        print("=" * 50)
        print(f"  PC      →  http://localhost:{PORT}")
        print(f"  Phone   →  http://{ip}:{PORT}")
        print()
        print("  Même WiFi (ou hotspot téléphone).")
        print("  Ctrl+C pour arrêter.")
        print("=" * 50)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
