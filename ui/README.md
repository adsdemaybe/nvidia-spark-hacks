# console

The PCB and CAD viewers in one page, because they are one robot.

```bash
python ui/console.py                       # http://<this-box>:8600
```

All three viewers on screen at once — the board on the left, the enclosure that
has to fit it top right, the robot that carries both underneath — with draggable
splitters between them and a `focus` button on each pane to fill the window.
Nothing here reimplements a viewer; each pane is an iframe over one that already
existed.

| pane | port | what you can do in it |
|---|---|---|
| PCB boards | 8500 | schematics, layout and DRC for the rover's four boards |
| CAD viewer | 3246 | the enclosures and the print plate — hide the `-lid` occurrence to see standoffs, ears and port cutouts |
| Joint viewer | 8081 | **drag a joint.** One slider per movable joint, plus a joint-axis toggle |

Services with no page of their own — the CAD API, the docs RAG, the model server,
and the two `127.0.0.1`-only viewers — are status pills in the header rather than
panes, because an API has nothing to show and a dead pane is worse than a dot.

**Focus hides, it never unmounts.** An iframe removed from the DOM reloads when it
comes back, which drops every joint slider position and the selected CAD model.
The layout is CSS only, so the panes keep their state across focus and resize; a
`resize` event is dispatched afterwards because Viser and the CAD viewer size
their canvas to a container that was `display: none`.

The joint viewer is the one that answers a question no static view can: the
sliders move the actual kinematics, so a wheel whose geometry axis and joint axis
disagree is visible as a wheel that orbits instead of spinning.

## Two things that were wrong before this existed

**Everything bound `127.0.0.1`.** Which is invisible from anywhere but the box
itself, and this box is used over SSH and Tailscale. The console binds `0.0.0.0`
and — the part that actually matters — builds every iframe URL from
`location.hostname`, so it works at `127.0.0.1`, `172.16.94.156` and
`100.82.201.40` without a config file. Hard-coding any one of those addresses is
the bug that makes a dashboard work for exactly one person.

**The CAD viewer showed an empty catalog.** Its directory comes from the URL path
and falls back to the *process's* working directory, so one started from the
skill's own folder browses the skill's own folder and finds no CAD. Start it from
the directory holding the models:

```bash
cd cad-generation/enclosures
PYTHONPATH=.claude/skills/cad-viewer/scripts/viewer \
  .agents/.venv/bin/python -m server_py.server --host 0.0.0.0 --port 3246 \
  --dist-root .claude/skills/cad-viewer/scripts/viewer/dist
```

## Status, and why it is probed server-side

A browser cannot read the response status of a cross-origin `fetch`, so a
page-side check can only report "something answered" — including for a port that
is refusing connections. `console.py` connects to each port itself and reports
the socket and the HTTP answer separately, because "nothing is listening" and
"listening but erroring" have different fixes.

## Adding a viewer

Append to `SERVICES` in `console.py`. `tab: True` gets an iframe; `tab: False` is
status-only, which is right for an API with no page in it. A second joint viewer
for another design is just another entry:

```bash
cad-generation/engine/.venv/bin/python -m cad_api.viewer \
    cad-generation/designs/rover_arm_3axis.ir.json --host 0.0.0.0 --port 8082
```

## Caveat

No authentication. On `0.0.0.0` anything that can route to this box can open the
viewers — which matches what `pcb-ai` on 8500 already does here. On a network you
do not control, bind the Tailscale address instead (`--host 100.82.201.40`).

## studio — the end that starts a run

```bash
python ui/studio.py                        # http://<this-box>:8610
```

Everything else here shows what the pipeline *has* produced. Studio is the prompt box:
describe a board, and F1 designs and gates it, then F2 fits an enclosure to the result.

| | |
|---|---|
| **F1 — board** | spec → parts plan → tscircuit HDL → the gate ladder (lint, compile, place, route, DRC, physics, SPICE, DFM, placement rules) → three reviewers → a verdict |
| **F2 — enclosure** | the finished board's report → cavity, standoffs, port cutouts → `check_fit` |

Output streams as it happens, verbatim. A spinner that resolves to "done" would hide the
interesting half: the gate ladder printing `compile 7 parts, 4 nets, 8 errors` and the
reviewers arguing about it is the reason to watch a run at all.

**A run ending in REVISE is the normal outcome**, not an error. No AI-generated board is
accepted yet (see the repo README), so the blockers are what you came for — the UI colours
them as findings rather than as failure.

Three things about the shape, since they are decisions rather than accidents:

- **It shells out and does not reimplement.** Each stage is the same command a person would
  type, so there is one definition of a stage and this page cannot drift from it.
- **Reconnecting replays the whole run**, not just the tail, so a closed laptop does not
  lose the log.
- `model` picks between the local endpoints; `stub` runs the whole ladder with no model at
  all, which is the fast way to check the plumbing.
