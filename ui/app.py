"""STRUCT — one console for the whole pipeline.

    python ui/app.py                     # http://<this-box>:8600

This replaces the two that came before it. `console.py` framed the viewers and told you
which were up; `studio.py` took a prompt and streamed the design loop. Running both meant
knowing which port did which, and neither could show the thing a person actually opens a
console for: **what has been built so far, and is it any good.**

So: one port, four views, and a status strip.

    Design    prompt a board; watch F1 gate it and F2 fit an enclosure, iteration by
              iteration, with the delta so you can see whether a fix helped
    Overview  every board and design on disk, grouped by robot rather than by directory
    Viewers   the PCB, CAD and joint viewers, framed
    Services  what is up, what is not, and what each one is for

Three properties worth keeping if this is ever rewritten:

**Nothing here recomputes a verdict.** The overview reads `report.txt` and `verdict.json`
and reports what the run concluded. A console that formed its own opinion about a board
would be wrong by construction the first time it disagreed with the pipeline that made it.

**Iframe hosts are built client-side from `location.hostname`.** Every service binds a
port on this machine and the address that reaches it depends on who is looking —
`127.0.0.1` on the box, a LAN address, a Tailscale address. Baking one in makes the page
work for exactly one viewer.

**Status is probed server-side.** A cross-origin `fetch` cannot read another port's status,
so a browser-side check can only report "something answered" — including for a service
refusing connections. This process connects itself.

Stdlib only, deliberately: this is a dev console on a box where the model server, the CAD
service and Isaac Sim already compete for memory. It is not worth a framework.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shlex
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PCB = ROOT / "pcb-ai"
PCB_RUNS = PCB / "runs"
ROBOTS_JSON = PCB / "robots.json"
CAD_DESIGNS = ROOT / "cad-generation" / "designs"

CAD_API = os.environ.get("CAD_API_URL", "http://127.0.0.1:8210")

# `node --env-file-if-exists=pcb-ai/.env`, not `npx tsx`. This console is a long-running
# systemd service, so it never inherits the API key from a shell someone typed `export`
# into — that env-file is the only way the key reaches it. `--env-file-if-exists` is a
# builtin flag on Node 24 (which this repo already requires), so a run on a machine with
# no .env is silent and unaffected rather than failing on a missing file.
TSX = ["node", "--env-file-if-exists=.env", "node_modules/.bin/tsx"]

# `tab` entries get a framed pane; the rest are status-only, because an API with no page
# in it has nothing to show and pretending otherwise wastes a tab.
SERVICES = [
    {"id": "pcb", "name": "PCB boards", "port": 8500, "tab": True,
     "note": "schematics, layouts and DRC for every board the pipeline has made"},
    {"id": "cad", "name": "CAD viewer", "port": 3246, "tab": True,
     "note": "STEP/STL/3MF — enclosures, parts and the print plate"},
    {"id": "joints", "name": "Joint viewer", "port": 8082, "tab": True,
     "note": "Viser — drag a joint and the link moves"},
    {"id": "cad-api", "name": "CAD API", "port": 8210, "probe": "/health",
     "note": "the PCB↔CAD contract — design_enclosure, check_fit, constrain_board"},
    {"id": "rag", "name": "Docs RAG", "port": 8220, "probe": "/health",
     "note": "tscircuit + build123d retrieval that grounds the design agents"},
    {"id": "vllm", "name": "Model server", "port": 8100, "probe": "/v1/models",
     "note": "vLLM — the model the design loop proposes with"},
    {"id": "gateway", "name": "OpenShell gateway", "port": 17670,
     "note": "sandboxed agent runs"},
    {"id": "joints-rover", "name": "Joint viewer — rover", "port": 8081,
     "note": "rover_4wd_300mm"},
]


# ── status ──────────────────────────────────────────────────────────────────────

def _probe(service: dict) -> dict:
    """Is the port open, and does its health path answer? Reported separately.

    A port that accepts a connection while its app returns 500 is a different problem
    from a port nothing is listening on, and the fix differs too.
    """
    port = service["port"]
    result = {"id": service["id"], "port": port, "open": False, "http": None}
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            result["open"] = True
    except OSError:
        return result
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{service.get('probe', '/')}", timeout=2.0
        ) as resp:
            result["http"] = resp.status
    except urllib.error.HTTPError as exc:
        result["http"] = exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        result["http"] = None
    return result


# ── overview ────────────────────────────────────────────────────────────────────

def _gate_summary(run_dir: Path) -> dict:
    """What a run concluded, read from its own artifacts.

    The last iteration that actually compiled, not iteration 0 — a first iteration is
    very often a lint failure with no compile behind it, and reporting that as the board's
    state makes every in-progress run look broken.
    """
    out: dict = {}
    iters = sorted(
        [d for d in run_dir.glob("iter-*") if (d / "report.txt").exists()],
        key=lambda d: d.name,
    )
    if iters:
        text = (iters[-1] / "report.txt").read_text(errors="replace")
        m = re.search(r"Compiler errors \((\d+)\)", text)
        out["errors"] = int(m.group(1)) if m else None
        m = re.search(r"Compiler warnings \((\d+)\)", text)
        out["warnings"] = int(m.group(1)) if m else None
        out["iterations"] = len(iters)
    for cand in reversed(iters):
        vj = cand / "verdict.json"
        if vj.exists():
            try:
                v = json.loads(vj.read_text())
                out["verdict"] = "ACCEPT" if v.get("pass") else "REVISE"
                out["blockers"] = sum(
                    1 for w in (v.get("work_order") or []) if w.get("severity") == "blocker"
                )
            except (ValueError, OSError):
                pass
            break
    if iters and (iters[-1] / "circuit.json").exists():
        try:
            els = json.loads((iters[-1] / "circuit.json").read_text())
            out["parts"] = sum(1 for e in els if e.get("type") == "pcb_component")
            out["holes"] = sum(1 for e in els if e.get("type") == "pcb_hole")
            out["traces"] = sum(1 for e in els if e.get("type") == "pcb_trace")
        except (ValueError, OSError):
            pass
    try:
        out["mtime"] = max(p.stat().st_mtime for p in run_dir.rglob("*") if p.is_file())
    except (OSError, ValueError):
        pass
    return out


def _overview() -> dict:
    """Every board and design on disk, grouped the way a person thinks about them.

    By robot, not by directory. `robots.json` records which runs belong to which robot and
    how many of each are fitted — two motor drivers from one design — and without that the
    page is a list of names only their author can read.
    """
    robots, claimed = [], set()
    if ROBOTS_JSON.exists():
        try:
            manifest = json.loads(ROBOTS_JSON.read_text())
        except (ValueError, OSError):
            manifest = {"robots": []}
        for r in manifest.get("robots", []):
            boards = []
            for b in r.get("boards", []):
                run = b["run"]
                claimed.add(run)
                d = PCB_RUNS / run
                boards.append({
                    "run": run, "role": b.get("role", ""), "count": b.get("count", 1),
                    "note": b.get("note", ""), "exists": d.exists(),
                    **(_gate_summary(d) if d.exists() else {}),
                })
            robots.append({
                "id": r.get("id"), "name": r.get("name"),
                "description": r.get("description", ""), "boards": boards,
            })

    # Runs nobody claimed. Shown rather than hidden: an unlisted run is usually one
    # somebody is mid-way through, and a console that only renders the manifest cannot
    # tell you that anything is happening.
    loose = []
    if PCB_RUNS.exists():
        for d in sorted(PCB_RUNS.iterdir()):
            if d.is_dir() and d.name not in claimed:
                loose.append({"run": d.name, "role": "", "count": 1, "exists": True,
                              **_gate_summary(d)})

    designs = []
    if CAD_DESIGNS.exists():
        for f in sorted(CAD_DESIGNS.glob("*.ir.json")):
            try:
                ir = json.loads(f.read_text())
                designs.append({
                    "name": ir.get("name", f.stem),
                    "links": len(ir.get("links", [])),
                    "joints": len(ir.get("joints", [])),
                    "electronics": bool(ir.get("electronics")),
                })
            except (ValueError, OSError):
                designs.append({"name": f.stem, "links": None, "joints": None,
                                "electronics": False})

    return {"robots": robots, "unclaimed": loose, "designs": designs}


# ── design runs ─────────────────────────────────────────────────────────────────

STAGE = "\x01stage\x02"
END = "\x01end\x02"


@dataclass
class Run:
    id: str
    spec: str
    model: str
    iterations: int
    started: float = field(default_factory=time.time)
    lines: list[str] = field(default_factory=list)
    done: bool = False
    subscribers: list["queue.Queue[str]"] = field(default_factory=list)

    def emit(self, line: str) -> None:
        # Under the same lock the stream handler uses: a line emitted between the backlog
        # snapshot and the subscriber being appended is otherwise lost or doubled.
        with LOCK:
            self.lines.append(line)
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(line)
            except queue.Full:
                pass


RUNS: dict[str, Run] = {}
LOCK = threading.Lock()


def _stream_command(run: Run, label: str, argv: list[str], cwd: Path) -> int:
    """One stage, streamed as it arrives.

    Line-buffered and unbuffered on the child side. Without that a four-minute stage
    delivers everything in its last second, which is indistinguishable from a hang — and
    this pipeline has four-minute stages.
    """
    run.emit(f"{STAGE}{label}")
    run.emit("$ " + " ".join(shlex.quote(a) for a in argv))
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "NODE_NO_WARNINGS": "1",
           "CAD_API_URL": CAD_API}
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
    except FileNotFoundError as exc:
        run.emit(f"!! cannot run {argv[0]}: {exc}")
        return 127
    assert proc.stdout is not None
    for line in proc.stdout:
        run.emit(line.rstrip("\n"))
    return proc.wait()


def _pipeline(run: Run) -> None:
    """spec → F1 gates → F2 enclosure fit."""
    try:
        outdir = PCB_RUNS / run.id
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "spec.md").write_text(run.spec, encoding="utf8")

        rc = _stream_command(run, "F1 — board", [
            *TSX, "src/cli.ts", "--spec", str(outdir / "spec.md"),
            "--model", run.model, "--iterations", str(run.iterations),
            "--out", str(outdir),
        ], PCB)

        circuit = outdir / "iter-0" / "circuit.json"
        iters = sorted(outdir.glob("iter-*/circuit.json"))
        if iters:
            circuit = iters[-1]
        # rc is deliberately not the verdict: the CLI exits non-zero when a board is not
        # accepted, which is the normal outcome today and still leaves a real board worth
        # looking at. What decides whether F2 can run is whether geometry exists.
        if not circuit.exists():
            run.emit(f"!! no circuit.json produced (exit {rc}) — nothing for F2 to fit")
            return

        _stream_command(run, "F2 — enclosure",
                        [*TSX, "src/cad/integration-check.ts", str(circuit)], PCB)
        run.emit(f"{STAGE}done")
    except Exception as exc:  # noqa: BLE001 — a crash here must still close the stream
        run.emit(f"!! console error: {type(exc).__name__}: {exc}")
    finally:
        run.done = True
        run.emit(END)


# ── server ──────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        if self.path.startswith("/api/status"):
            return self._json({"services": [
                {**s, **_probe(s)} for s in SERVICES
            ]})
        if self.path.startswith("/api/overview"):
            return self._json(_overview())
        if self.path.startswith("/stream/"):
            return self._stream(self.path.rsplit("/", 1)[-1])
        if self.path in ("/", "/index.html"):
            return self._send(200, "text/html; charset=utf-8",
                              (HERE / "app.html").read_bytes())
        self._send(404, "text/plain; charset=utf-8", b"not found\n")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run":
            return self._send(404, "text/plain", b"not found")
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        spec = str(body.get("spec") or "").strip()
        if not spec:
            return self._json({"error": "empty spec"}, status=400)
        raw_iters = body.get("iterations")
        run = Run(
            id=f"studio-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}",
            spec=spec,
            model=str(body.get("model") or "coder-next"),
            # `or 3` would turn an explicit 0 into 3, because 0 is falsy.
            iterations=max(0, min(8, int(raw_iters if raw_iters is not None else 3))),
        )
        with LOCK:
            RUNS[run.id] = run
        threading.Thread(target=_pipeline, args=(run,), daemon=True).start()
        self._json({"id": run.id})

    def _stream(self, run_id: str) -> None:
        run = RUNS.get(run_id)
        if run is None:
            return self._send(404, "text/plain", b"no such run")
        q: queue.Queue[str] = queue.Queue(maxsize=10000)
        with LOCK:
            backlog = list(run.lines)
            run.subscribers.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            for line in backlog:
                self._event(line)
            while True:
                try:
                    self._event(q.get(timeout=20))
                except queue.Empty:
                    # Model stages routinely go a minute without printing; a comment frame
                    # stops proxies and browsers closing an idle stream.
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with LOCK:
                if q in run.subscribers:
                    run.subscribers.remove(q)

    def _event(self, line: str) -> None:
        self.wfile.write(f"data: {json.dumps(line)}\n\n".encode())
        self.wfile.flush()

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, "application/json", json.dumps(obj).encode())

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return  # the run log is the output that matters; access logs drown it


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8600)
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"STRUCT console on http://{a.host}:{a.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
