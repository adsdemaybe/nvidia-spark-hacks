"""One page for the PCB and CAD viewers, because they are one robot.

    python ui/console.py                 # http://<this-box>:8600
    python ui/console.py --port 8600 --host 0.0.0.0

Each viewer already exists and each is good at its own job; what did not exist
was a place to see them together. This does not reimplement any of them — it
frames them, and it tells you which ones are actually up.

Two design notes, both about failures already hit on this box:

1. **The iframes are built client-side from `location.hostname`.** Every service
   here binds a port on this machine, and the address that reaches it depends on
   who is looking: `127.0.0.1` on the box, `172.16.94.156` over the LAN,
   `100.82.201.40` over Tailscale. Baking any one of those into the page makes it
   work for exactly one viewer. The page uses whatever host you typed.

2. **The status strip probes from the server, not the browser.** A cross-origin
   `fetch` from the page cannot read another port's response status, so a
   browser-side check can only ever report "something answered" — including for a
   service that is refusing connections. This process connects to each port
   itself and reports what it found.

Stdlib only, and deliberately: this is a dev console on a box where the CAD
service, the model server and Isaac Sim already compete for memory, and it is
not worth a framework.
"""

from __future__ import annotations

import argparse
import json
import socket
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent

# `tab` entries get an iframe; the rest are status-only, because an API with no
# page in it has nothing to show and pretending otherwise wastes a tab.
SERVICES = [
    {"id": "pcb", "name": "PCB boards", "port": 8500, "tab": True,
     "note": "pcb-ai — schematics, layouts and DRC for the rover's four boards"},
    {"id": "cad", "name": "CAD viewer", "port": 3246, "tab": True,
     "note": "STEP/STL/3MF — the board enclosures and the print plate"},
    # `rover_arm_3axis` rather than the plain rover, because this pane exists to
    # show articulation and a wheel does not show it: a tube turning about its own
    # axis is rotationally symmetric, so a correctly spinning wheel renders
    # identically at every angle. The arm's four joints move links you can see.
    # The wheels are still here — this design carries both.
    {"id": "joints", "name": "Joint viewer", "port": 8082, "tab": True,
     "note": "Viser — rover_arm_3axis, 8 movable joints; drag one and the link moves"},
    {"id": "cad-api", "name": "CAD API", "port": 8210, "tab": False, "probe": "/health",
     "note": "the §6 PCB↔CAD contract — design_enclosure, check_fit, constrain_board"},
    {"id": "rag", "name": "Docs RAG", "port": 8220, "tab": False, "probe": "/health",
     "note": "tscircuit + build123d retrieval that grounds the design agents"},
    {"id": "vllm", "name": "Model server", "port": 8100, "tab": False, "probe": "/v1/models",
     "note": "vLLM — the model the design loop proposes with"},
    {"id": "joints-rover", "name": "Joint viewer — 4WD rover", "port": 8081, "tab": False,
     "note": "rover_4wd_300mm — the design that passes tier 2 at 15977mm of `drives`"},
    {"id": "cad-3245", "name": "CAD viewer (127.0.0.1 only)", "port": 3245, "tab": False,
     "note": "the pre-existing instance; reachable from the box itself or an ssh tunnel"},
    {"id": "arm", "name": "Joint viewer — SO-101 (127.0.0.1 only)", "port": 8080, "tab": False,
     "note": "the pre-existing instance, loaded with so101_arm.ir.json"},
]


def _probe(service: dict) -> dict:
    """Is the port open, and does its health path answer? Reported separately.

    A port that accepts a connection while its app returns 500 is a different
    problem from a port nothing is listening on, and the fix is different too.
    """
    port = service["port"]
    result = {"id": service["id"], "port": port, "open": False, "http": None}
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            result["open"] = True
    except OSError:
        return result

    path = service.get("probe", "/")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2.0) as resp:
            result["http"] = resp.status
    except urllib.error.HTTPError as exc:
        result["http"] = exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        result["http"] = None
    return result


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        if self.path.startswith("/api/status"):
            body = json.dumps({
                "services": [_probe(s) for s in SERVICES],
                "meta": {s["id"]: {k: s[k] for k in ("name", "port", "tab", "note")} for s in SERVICES},
            }).encode()
            return self._send(200, "application/json", body)

        if self.path in ("/", "/index.html"):
            return self._send(200, "text/html; charset=utf-8", (HERE / "index.html").read_bytes())

        self._send(404, "text/plain; charset=utf-8", b"not found\n")

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return  # a status poll every few seconds would drown anything worth reading


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--host", default="0.0.0.0",
                    help="0.0.0.0 so the LAN and Tailscale addresses both work")
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"STRUCT console on http://{args.host}:{args.port}")
    for s in SERVICES:
        state = _probe(s)
        mark = "up  " if state["open"] else "DOWN"
        print(f"  {mark} :{s['port']:<5} {s['name']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
