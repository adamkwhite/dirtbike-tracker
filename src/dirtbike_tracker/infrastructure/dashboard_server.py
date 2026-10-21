"""
The dashboard over HTTP, with the hide/unhide button behind it (`dirtbike serve`).

Serves one file at `/` and nothing else, so `data/` (the DB, contact notes) is never
reachable. POST /hide {"id", "hidden", "note"} saves through the `hide` callback, which
also rebuilds the page; POST /note {"id", "note"} saves a listing's note through `note`.
Requests must carry `X-Dirtbike: 1`: a custom header makes a
browser preflight a cross-site request, and there is no OPTIONS handler, so another site
can't hide bikes through the viewer's browser. Bind to localhost; tailscale serve proxies it.
"""

import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# (listing id, hidden, note) -> whether the listing exists
Hide = Callable[[int, bool, str | None], bool]
# (listing id, note) -> whether the listing exists
Note = Callable[[int, str | None], bool]


def make_server(
    page: Path, hide: Hide, note: Note, host: str = "127.0.0.1", port: int = 8765
) -> HTTPServer:
    """One request at a time (ponytail: a single viewer), so two hides never rebuild at once."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.split("?")[0] not in ("/", "/index.html"):
                self.send_error(404)
                return
            body = page.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            # body is the dashboard file this app wrote, nothing from the request (Sonar: false XSS)
            self.wfile.write(body)  # NOSONAR

        def do_POST(self) -> None:
            if self.path not in ("/hide", "/note"):
                self.send_error(404)
                return
            if self.headers.get("X-Dirtbike") != "1":
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(min(length, 10_000)))
                listing_id = int(req["id"])
                hidden = bool(req["hidden"]) if self.path == "/hide" else False
                text = str(req.get("note") or "").strip()[: 200 if self.path == "/hide" else 1000]
            except (ValueError, KeyError, TypeError):
                self.send_error(400)
                return
            if self.path == "/hide":
                found = hide(listing_id, hidden, text or None)
            else:
                found = note(listing_id, text or None)
            if not found:
                self.send_error(404)
                return
            self.send_response(204)
            self.end_headers()

    return HTTPServer((host, port), Handler)
