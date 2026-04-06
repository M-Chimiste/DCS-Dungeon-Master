"""HTTP server for the Milestone 10 web UI API."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from dcs_dungeon_master.web_ui.api import WebUiService


def serve_web_ui(
    service: WebUiService,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    static_dir: str | Path | None = None,
) -> None:
    static_root = Path(static_dir) if static_dir is not None else Path("frontend/dist")
    attachment_root = Path(service.config.multimodal.output_dir)
    map_asset_root = service.map_asset_service.asset_root if service.map_asset_service is not None else None

    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_cors_headers()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._handle("PUT")

        def do_PATCH(self) -> None:  # noqa: N802
            self._handle("PATCH")

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle("DELETE")

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _handle(self, method: str) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                try:
                    if method == "GET" and parsed.path.endswith("/export.toml"):
                        draft_id = parsed.path.strip("/").split("/")[-2]
                        export = service.draft_service.export_draft_toml(draft_id)
                        self._send_text(HTTPStatus.OK, export.toml, content_type=export.media_type, filename=export.filename)
                        return
                    body = self._read_json_body() if method in {"POST", "PUT", "PATCH", "DELETE"} else {}
                    status, payload = service.dispatch(method, parsed.path, body)
                    self._send_json(status, payload)
                    return
                except json.JSONDecodeError:
                    self._send_json(400, {"error": "Request body must be valid JSON."})
                    return
            if self._serve_public_file(parsed.path):
                return
            self._serve_app_shell(parsed.path)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                return {"value": payload}
            return payload

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")
            self.send_response(status)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, status: int, payload: str, *, content_type: str, filename: str | None = None) -> None:
            body = payload.encode("utf-8")
            self.send_response(status)
            self._send_cors_headers()
            self.send_header("Content-Type", content_type)
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _serve_public_file(self, path: str) -> bool:
            if path.startswith("/attachments/"):
                candidate = self._resolve_public_path(attachment_root, path.removeprefix("/attachments/"))
                if candidate is not None:
                    self._send_file(candidate)
                    return True
            if map_asset_root is not None and path.startswith("/map-assets/"):
                candidate = self._resolve_public_path(map_asset_root, path.removeprefix("/map-assets/"))
                if candidate is not None:
                    self._send_file(candidate)
                    return True
            return False

        def _resolve_public_path(self, root: Path, relative: str) -> Path | None:
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root.resolve())
            except Exception:  # noqa: BLE001
                return None
            return candidate if candidate.is_file() else None

        def _send_file(self, path: Path) -> None:
            data = path.read_bytes()
            media_type, _ = mimetypes.guess_type(path.name)
            self.send_response(HTTPStatus.OK)
            self._send_cors_headers()
            self.send_header("Content-Type", media_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve_app_shell(self, path: str) -> None:
            if static_root.exists() and static_root.is_dir():
                candidate = static_root / path.lstrip("/")
                if candidate.is_file():
                    self._send_file(candidate)
                    return
                index_path = static_root / "index.html"
                if index_path.exists():
                    data = index_path.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>DCS Dungeon Master UI</title>
    <style>
      body { font-family: Georgia, serif; background: #f4efe3; color: #1f2937; margin: 0; padding: 3rem; }
      .card { max-width: 760px; background: rgba(255,255,255,0.7); border: 1px solid #b08968; padding: 2rem; border-radius: 16px; }
      a { color: #8c2f39; }
      code { background: #f5e6cc; padding: 0.15rem 0.3rem; border-radius: 4px; }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>DCS Dungeon Master Web UI API</h1>
      <p>The Python API is running. Build or run the Vite frontend separately for the full Studio and Ops UI.</p>
      <p>Try <a href="/api/scenarios">/api/scenarios</a> or <a href="/api/runs">/api/runs</a>.</p>
      <p>Suggested frontend command: <code>npm install</code> then <code>npm run dev</code> in <code>frontend/</code>.</p>
    </div>
  </body>
</html>
"""
            data = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
