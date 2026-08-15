"""Studio — type what you want, watch the pipeline build it.

    python ui/studio.py            # http://<this-box>:8610

Everything else in `ui/` shows what the pipeline *has* produced. This is the end that
starts one: a prompt box, and the F1 -> F2 chain running behind it with its output
streamed back line by line.

Three things worth knowing about the shape:

**It shells out; it does not reimplement.** Each stage is the same command a person would
type — `src/cli.ts --spec …`, `src/cad/integration-check.ts …` — so there is exactly one
definition of what a stage does and this file cannot drift from it. The cost is a
subprocess per stage, which against a design loop measured in minutes is nothing.

**The log is the product.** A spinner that resolves to "done" hides the interesting half.
The gate ladder prints `compile 7 parts, 4 nets, 8 errors` and three reviewers' findings,
and that text is why you would watch a run at all, so it is streamed verbatim rather than
summarised into a status.

**A failed run is a result, not an error page.** No AI-generated board is accepted yet
(README, "What works, and what does not"), so the common outcome is REVISE with blockers.
The UI has to make that legible instead of red — the failure text is the thing you came
for.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import queue
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PCB = ROOT / "pcb-ai"

VIEWER_PORT = int(os.environ.get("PCB_VIEWER_PORT", "8500"))
CAD_API = os.environ.get("CAD_API_URL", "http://127.0.0.1:8210")


@dataclass
class Run:
    id: str
    spec: str
    model: str
    iterations: int
    started: float = field(default_factory=time.time)
    lines: list[str] = field(default_factory=list)
    done: bool = False
    ok: bool = False
    subscribers: list["queue.Queue[str]"] = field(default_factory=list)

    def emit(self, line: str) -> None:
        # Under the same lock the stream handler uses. Without it a line emitted between
        # the backlog snapshot and the subscriber being appended is either lost or
        # delivered twice, depending on which side of the append it lands.
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


def stream_command(run: Run, label: str, argv: list[str], cwd: Path) -> int:
    """Run one stage, streaming its output into the run log as it arrives.

    Line-buffered and unbuffered on the child side. Without that, a stage that takes four
    minutes delivers its entire output in the last second, which looks identical to a hang
    — and this pipeline has stages that take four minutes.
    """
    run.emit(f"\x01stage\x02{label}")
    run.emit(f"$ {' '.join(shlex.quote(a) for a in argv)}")
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "NODE_NO_WARNINGS": "1", "CAD_API_URL": CAD_API}
    try:
        proc = subprocess.Popen(
            argv, cwd=str(cwd), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except FileNotFoundError as exc:
        run.emit(f"!! cannot run {argv[0]}: {exc}")
        return 127
    assert proc.stdout is not None
    for line in proc.stdout:
        run.emit(line.rstrip("\n"))
    return proc.wait()


def pipeline(run: Run) -> None:
    """spec -> F1 gates -> F2 enclosure fit."""
    try:
        outdir = PCB / "runs" / run.id
        outdir.mkdir(parents=True, exist_ok=True)
        spec_path = outdir / "spec.md"
        spec_path.write_text(run.spec, encoding="utf8")

        rc = stream_command(
            run, "F1 — board",
            ["npx", "tsx", "src/cli.ts",
             "--spec", str(spec_path),
             "--model", run.model,
             "--iterations", str(run.iterations),
             "--out", str(outdir)],
            PCB,
        )
        circuit = outdir / "iter-0" / "circuit.json"
        # rc is deliberately not the verdict. The CLI exits non-zero when a board is not
        # accepted, which is the normal outcome today and still produces a real board worth
        # looking at. What decides whether F2 can run is whether geometry exists.
        if not circuit.exists():
            run.emit(f"!! no circuit.json produced (exit {rc}) — nothing for F2 to fit")
            run.ok = False
            return

        rc2 = stream_command(
            run, "F2 — enclosure",
            ["npx", "tsx", "src/cad/integration-check.ts", str(circuit)],
            PCB,
        )
        run.emit(f"\x01stage\x02done")
        run.emit(f"viewer: http://<this-box>:{VIEWER_PORT}/  (run '{run.id}')")
        run.ok = rc2 == 0
    except Exception as exc:  # noqa: BLE001 — a crash here must still close the stream
        run.emit(f"!! studio error: {type(exc).__name__}: {exc}")
        run.ok = False
    finally:
        run.done = True
        run.emit("\x01end\x02")


# Raw, so every backslash escape reaches JavaScript untouched and JS interprets it.
# Non-raw, Python eats them first: `\[` in a regex is not a valid Python escape and warns,
# while `\u0001` becomes a literal control byte embedded in the HTML source. Both happen to
# work, which is worse than either failing.
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>STRUCT Studio</title>
<style>
:root{
  --bg:#f6f7f9; --panel:#fff; --ink:#14171c; --muted:#626a76; --line:#dfe3e9;
  --accent:#1f6feb; --ok:#1a7f45; --warn:#9a6700; --bad:#b42318;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#0d1117; --panel:#161b22; --ink:#e6edf3; --muted:#8b949e; --line:#30363d;
  --accent:#4493f8; --ok:#3fb950; --warn:#d29922; --bad:#f85149;
}}
:root[data-theme=dark]{
  --bg:#0d1117; --panel:#161b22; --ink:#e6edf3; --muted:#8b949e; --line:#30363d;
  --accent:#4493f8; --ok:#3fb950; --warn:#d29922; --bad:#f85149;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  display:flex;flex-direction:column;height:100vh}
header{display:flex;align-items:baseline;gap:14px;padding:12px 18px;
  border-bottom:1px solid var(--line);background:var(--panel)}
header h1{font-size:15px;margin:0;letter-spacing:.02em}
header .sub{color:var(--muted);font-size:13px}
header a{color:var(--accent);font-size:13px;text-decoration:none;margin-left:auto}
main{flex:1;display:grid;grid-template-columns:minmax(320px,420px) 1fr;min-height:0}
@media (max-width:820px){main{grid-template-columns:1fr;grid-template-rows:auto 1fr}}
.left{padding:16px;border-right:1px solid var(--line);overflow:auto;background:var(--panel)}
label{display:block;font-size:12px;color:var(--muted);margin:12px 0 5px;
  text-transform:uppercase;letter-spacing:.06em}
textarea,select,input{width:100%;background:var(--bg);color:var(--ink);
  border:1px solid var(--line);border-radius:7px;padding:9px 10px;font-size:14px;font-family:inherit}
textarea{min-height:190px;resize:vertical;font-family:var(--mono);font-size:13px;line-height:1.5}
.row{display:flex;gap:10px}.row>*{flex:1}
button{margin-top:14px;width:100%;background:var(--accent);color:#fff;border:0;
  border-radius:7px;padding:11px;font-size:14px;font-weight:600;cursor:pointer}
button[disabled]{opacity:.5;cursor:progress}
.examples{margin-top:14px;font-size:13px}
.examples b{display:block;color:var(--muted);font-size:12px;text-transform:uppercase;
  letter-spacing:.06em;margin-bottom:6px}
.examples a{display:block;color:var(--accent);text-decoration:none;padding:3px 0;cursor:pointer}
.right{display:flex;flex-direction:column;min-height:0}
.stages{display:flex;gap:6px;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--line)}
.pill{font-size:12px;padding:3px 10px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.pill.active{border-color:var(--accent);color:var(--accent)}
.pill.done{border-color:var(--ok);color:var(--ok)}
#log{flex:1;overflow:auto;margin:0;padding:14px 16px;font-family:var(--mono);
  font-size:12.5px;line-height:1.55;white-space:pre-wrap;word-break:break-word}
#log .cmd{color:var(--muted)}
#log .err{color:var(--bad)}
#log .good{color:var(--ok)}
#log .warnl{color:var(--warn)}
#log .head{color:var(--accent);font-weight:600;display:block;margin-top:10px}
.empty{color:var(--muted);padding:24px 16px;max-width:64ch;white-space:pre-wrap;font-size:13.5px}
.tabs{margin-left:auto;display:flex;gap:4px}
.tab{font-size:12px;padding:3px 10px;border-radius:6px;color:var(--muted);cursor:pointer;
  border:1px solid transparent}
.tab.on{color:var(--ink);border-color:var(--line);background:var(--bg)}
#timeline{flex:1;overflow:auto;padding:14px 16px}
.card{border:1px solid var(--line);border-radius:9px;background:var(--panel);
  margin-bottom:12px;overflow:hidden}
.card>h3{margin:0;padding:9px 13px;font-size:13px;display:flex;align-items:center;gap:9px;
  border-bottom:1px solid var(--line);background:var(--bg)}
.card>h3 .delta{margin-left:auto;font-size:12px;font-weight:600}
.delta.worse{color:var(--bad)} .delta.better{color:var(--ok)} .delta.same{color:var(--muted)}
.metrics{display:flex;flex-wrap:wrap;gap:6px;padding:10px 13px}
.metric{font-size:12px;font-family:var(--mono);padding:2px 8px;border-radius:5px;
  border:1px solid var(--line);color:var(--muted)}
.metric.bad{border-color:var(--bad);color:var(--bad)}
.metric.good{border-color:var(--ok);color:var(--ok)}
.fixes{padding:2px 13px 12px}
.fixes h4{margin:8px 0 6px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);font-weight:600}
.fix{display:flex;gap:8px;font-size:13px;padding:4px 0;line-height:1.45}
.fix .sev{flex:none;font-size:10px;font-weight:700;text-transform:uppercase;padding:1px 6px;
  border-radius:4px;height:fit-content;margin-top:2px}
.sev.blocker{background:var(--bad);color:#fff}
.sev.major{background:var(--warn);color:#fff}
.sev.minor{background:var(--line);color:var(--muted)}
.applied{padding:9px 13px;border-top:1px solid var(--line);font-size:13px;color:var(--accent);
  background:var(--bg)}
.applied.none{color:var(--muted)}
</style></head><body>
<header>
  <h1>STRUCT Studio</h1>
  <span class="sub">describe a board — F1 designs and gates it, F2 fits an enclosure</span>
  <a href="http://__HOST__:8600/" target="_blank">console →</a>
</header>
<main>
  <div class="left">
    <label for="spec">Specification</label>
    <textarea id="spec" placeholder="A small board with two status LEDs a microcontroller can drive over a 2-pin header, powered from 3V3."></textarea>
    <div class="row">
      <div>
        <label for="model">Model</label>
        <select id="model">
          <option value="coder-next">coder-next (local)</option>
          <option value="nemotron">nemotron (local, vision)</option>
          <option value="stub">stub (no model)</option>
        </select>
      </div>
      <div>
        <label for="iters">Iterations</label>
        <input id="iters" type="number" min="0" max="8" value="3"/>
      </div>
    </div>
    <button id="go">Design it</button>
    <div class="examples"><b>Try</b>
      <a data-spec="A small board with two status LEDs a microcontroller can drive over a 2-pin header, powered from 3V3. Keep it under 25x20mm.">two-LED status indicator</a>
      <a data-spec="A power distribution board: 2S LiPo in on a 2-pin header, reverse-polarity protection with a P-FET, bulk capacitance for four motors starting together, and three fan-out headers.">power distribution</a>
      <a data-spec="A dual H-bridge motor driver board using a DRV8833, driving two DC motors from a 7.4V pack, with logic inputs on a 6-pin header and decoupling close to the driver.">motor driver</a>
    </div>
  </div>
  <div class="right">
    <div class="stages">
      <span class="pill" id="p-f1">F1 — board</span>
      <span class="pill" id="p-f2">F2 — enclosure</span>
      <span class="pill" id="p-done">done</span>
      <span class="tabs">
        <a id="t-play" class="tab on">play-by-play</a><a id="t-raw" class="tab">raw log</a>
      </span>
    </div>
    <div id="timeline">
      <div class="empty">Describe a board and press <b>Design it</b>.

F1 turns the specification into a parts plan, then tscircuit HDL, then runs the gate ladder
— lint, compile, place, route, DRC, physics, SPICE, DFM, placement rules — and three
reviewers argue about the result. Each iteration appears here as a card: what the gates
measured, what the reviewers want fixed, and what the model changed in response.

A run ending in REVISE is the normal outcome today. The blockers are the interesting part.</div>
    </div>
    <pre id="log" hidden></pre>
  </div>
</main>
<script>
const $ = s => document.querySelector(s);
// Named, because slicing a fixed 2 off a 7-character sentinel silently renders
// "tageF1 - board" and looks like a typo in the stage name rather than an off-by-five.
const STAGE = '\u0001stage\u0002', END = '\u0001end\u0002';

document.querySelectorAll('.examples a').forEach(a =>
  a.onclick = () => { $('#spec').value = a.dataset.spec; });

$('#t-play').onclick = () => {
  $('#t-play').classList.add('on'); $('#t-raw').classList.remove('on');
  $('#timeline').hidden = false; $('#log').hidden = true;
};
$('#t-raw').onclick = () => {
  $('#t-raw').classList.add('on'); $('#t-play').classList.remove('on');
  $('#timeline').hidden = true; $('#log').hidden = false;
};

// ---- the play-by-play ------------------------------------------------------
// Parsed from the CLI's own output rather than from a second machine-readable channel.
// One format to keep in step instead of two, and what you see is literally what the
// pipeline said. The cost is that a change to the CLI's wording lands here; the raw-log
// tab is always there underneath when this parser does not recognise something.
let iters = [], cur = null, prevErrors = null;

function num(re, line){ const m = line.match(re); return m ? +m[1] : null; }

function card(it, idx){
  const el = document.createElement('div'); el.className = 'card';
  const h = document.createElement('h3');
  h.appendChild(document.createTextNode('Iteration ' + it.n));

  // The delta is the whole point of a play-by-play: not "what does it say now" but
  // "did the last round of fixes help". A revise loop that makes a board worse looks
  // identical to one that helps unless the numbers are put side by side.
  if (it.errors !== null && it.prev !== null && it.prev !== undefined) {
    const d = it.errors - it.prev, sp = document.createElement('span');
    sp.className = 'delta ' + (d > 0 ? 'worse' : d < 0 ? 'better' : 'same');
    sp.textContent = d > 0 ? ('\u25b2 +' + d + ' errors — worse')
                   : d < 0 ? ('\u25bc ' + d + ' errors — better')
                           : 'no change in errors';
    h.appendChild(sp);
  }
  el.appendChild(h);

  const m = document.createElement('div'); m.className = 'metrics';
  for (const [label, val, bad] of it.metrics) {
    const b = document.createElement('span');
    b.className = 'metric' + (bad === true ? ' bad' : bad === false ? ' good' : '');
    b.textContent = label + ' ' + val;
    m.appendChild(b);
  }
  if (it.metrics.length) el.appendChild(m);

  if (it.fixes.length) {
    const f = document.createElement('div'); f.className = 'fixes';
    const t = document.createElement('h4'); t.textContent = 'what the reviewers want fixed';
    f.appendChild(t);
    for (const {sev, text} of it.fixes) {
      const row = document.createElement('div'); row.className = 'fix';
      const s1 = document.createElement('span'); s1.className = 'sev ' + sev; s1.textContent = sev;
      const s2 = document.createElement('span'); s2.textContent = text;
      row.appendChild(s1); row.appendChild(s2); f.appendChild(row);
    }
    el.appendChild(f);
  }

  if (it.applied !== null) {
    const a = document.createElement('div');
    a.className = 'applied' + (it.applied ? '' : ' none');
    a.textContent = it.applied
      ? ('\u270e the model applied ' + it.applied + ' edit block' + (it.applied === 1 ? '' : 's') + ' in response')
      : '\u270e no edits applied';
    el.appendChild(a);
  }
  return el;
}

function render(){
  const tl = $('#timeline'); tl.textContent = '';
  iters.forEach((it, i) => tl.appendChild(card(it, i)));
  tl.scrollTop = tl.scrollHeight;
}

function newIteration(n){
  cur = {n, metrics: [], fixes: [], applied: null, errors: null, prev: prevErrors};
  iters.push(cur);
}

function feed(line){
  const it = line.match(/^\u2500\u2500 iteration ([0-9]+)/);
  if (it) { newIteration(+it[1]); render(); return; }
  if (!cur) return;

  if (/^ {2}lint/.test(line)) {
    const e = num(/([0-9]+) error/, line);
    cur.metrics.push(['lint', e ? e + ' errors' : 'clean', e ? true : false]);
  } else if (/^ {2}compile/.test(line)) {
    const e = num(/([0-9]+) errors/, line), p = num(/([0-9]+) parts/, line);
    cur.errors = e;
    cur.metrics.push(['compile', (p !== null ? p + ' parts, ' : '') + e + ' errors', e > 0]);
  } else if (/^ {2}physics/.test(line)) {
    const d = num(/([0-9]+) DRC/, line), pk = line.match(/peak ([0-9.]+)/);
    cur.metrics.push(['physics', (pk ? pk[1] + '\u00b0C, ' : '') + d + ' DRC', d > 0]);
  } else if (/^ {2}(spice|dfm|placement)/.test(line)) {
    const name = line.trim().split(/ +/)[0];
    const e = num(/([0-9]+) (?:error|violation)/, line);
    if (e !== null) cur.metrics.push([name, e + (name === 'placement' ? ' violations' : ' errors'), e > 0]);
  } else if (/^ {2}verdict/.test(line)) {
    const b = num(/([0-9]+) blocker/, line);
    const v = line.includes('ACCEPT') ? 'ACCEPT' : 'REVISE';
    cur.metrics.push(['verdict', v + (b !== null ? ' \u00b7 ' + b + ' blockers' : ''),
                      v === 'ACCEPT' ? false : true]);
  } else if (line.includes('\u00b7 [')) {
    const m = line.match(/\u00b7 \[(blocker|major|minor)\] (.*)$/);
    if (m) cur.fixes.push({sev: m[1], text: m[2].trim()});
  } else if (/applied ([0-9]+) edit block/.test(line)) {
    cur.applied = num(/applied ([0-9]+) edit block/, line);
    prevErrors = cur.errors;
  }
  render();
}

$('#go').onclick = async () => {
  const spec = $('#spec').value.trim();
  if (!spec) { $('#spec').focus(); return; }
  iters = []; cur = null; prevErrors = null;
  $('#timeline').textContent = ''; $('#log').textContent = '';
  ['p-f1','p-f2','p-done'].forEach(id => $('#'+id).className = 'pill');
  $('#go').disabled = true; $('#go').textContent = 'Designing\u2026';

  const res = await fetch('/run', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({spec, model: $('#model').value, iterations: +$('#iters').value})});
  const {id} = await res.json();

  const es = new EventSource('/stream/' + id);
  es.onmessage = (ev) => {
    const line = JSON.parse(ev.data);
    if (line === END) {
      es.close(); $('#go').disabled = false; $('#go').textContent = 'Design it';
      $('#p-done').className = 'pill done'; return;
    }
    if (line.startsWith(STAGE)) {
      const name = line.slice(STAGE.length);
      if (name.startsWith('F1')) $('#p-f1').className = 'pill active';
      if (name.startsWith('F2')) { $('#p-f1').className='pill done'; $('#p-f2').className='pill active'; }
      if (name === 'done') $('#p-f2').className = 'pill done';
      $('#log').appendChild(document.createTextNode('\n== ' + name + ' ==\n'));
      return;
    }
    $('#log').appendChild(document.createTextNode(line + '\n'));
    if ($('#t-raw').classList.contains('on')) $('#log').scrollTop = $('#log').scrollHeight;
    feed(line);
  };
  es.onerror = () => { es.close(); $('#go').disabled = false; $('#go').textContent = 'Design it'; };
};
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        if self.path == "/" or self.path.startswith("/?"):
            host = (self.headers.get("Host") or "localhost").split(":")[0]
            self._send(200, "text/html; charset=utf-8",
                       PAGE.replace("__HOST__", html.escape(host)).encode())
            return
        if self.path.startswith("/stream/"):
            self._stream(self.path.rsplit("/", 1)[-1])
            return
        self._send(404, "text/plain", b"not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run":
            self._send(404, "text/plain", b"not found")
            return
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        spec = str(body.get("spec") or "").strip()
        if not spec:
            self._send(400, "application/json", b'{"error":"empty spec"}')
            return
        raw_iters = body.get("iterations")
        run = Run(
            id=f"studio-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}",
            spec=spec,
            model=str(body.get("model") or "coder-next"),
            # `or 3` here would turn an explicit 0 into 3, because 0 is falsy — which is
            # exactly what happened: a run asked for 0 iterations and did three.
            iterations=max(0, min(8, int(raw_iters if raw_iters is not None else 3))),
        )
        with LOCK:
            RUNS[run.id] = run
        threading.Thread(target=pipeline, args=(run,), daemon=True).start()
        self._send(200, "application/json", json.dumps({"id": run.id}).encode())

    def _stream(self, run_id: str) -> None:
        run = RUNS.get(run_id)
        if run is None:
            self._send(404, "text/plain", b"no such run")
            return
        q: queue.Queue[str] = queue.Queue(maxsize=10000)
        # Replay what already happened before subscribing, so a reconnecting browser sees
        # the whole run rather than only the tail. Under the lock, or a line emitted
        # between the replay and the append is lost.
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
                    # A comment frame keeps proxies and browsers from closing an idle
                    # stream. Model stages routinely go a minute without printing.
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

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return  # the run log is the interesting output; access logs drown it


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8610)
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"studio on http://{a.host}:{a.port}  (pcb-ai at {PCB})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
