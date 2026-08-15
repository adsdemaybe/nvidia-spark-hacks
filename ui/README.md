# console

One page for the whole pipeline.

```bash
python ui/app.py                 # http://<this-box>:8600
```

There used to be three of these — a console that framed the viewers, a studio that took a
prompt, and the viewers themselves — and running them meant knowing which port did which.
This is one port, four views, and a status strip.

| view | what it is for |
|---|---|
| **Design** | prompt a board; watch F1 gate it and F2 fit an enclosure, iteration by iteration |
| **Overview** | every board and design on disk, grouped by robot rather than by directory |
| **PCB / CAD / Joints** | the existing viewers, framed |
| **Services** | what is up, what is not, and what each one is for |

Keyboard: `1`–`6` switch views. The theme button toggles light/dark and remembers.

## Three properties worth keeping

**Nothing here recomputes a verdict.** The overview reads each run's own `report.txt` and
`verdict.json` and reports what that run concluded. A console with its own opinion about a
board would be wrong by construction the first time it disagreed with the pipeline that
made it. It also reads the *last iteration that compiled*, not iteration 0 — a first
iteration is very often a lint failure with no compile behind it, and reporting that as the
board's state makes every in-progress run look broken.

**Iframe hosts are built from `location.hostname`.** Every service binds a port on this
machine, and the address that reaches it depends on who is looking: `127.0.0.1` on the box,
a LAN address, a Tailscale address. Baking one in makes the page work for exactly one
viewer. Frames also mount on first use and are never unmounted — an iframe removed from the
DOM reloads when it returns, and a CAD viewer reload discards the camera you just set.

**Status is probed server-side.** A cross-origin `fetch` cannot read another port's status,
so a browser-side check can only report "something answered" — including for a service that
is refusing connections. This process connects to each port itself, and reports "not
listening" separately from "open, no answer", because those have different fixes.

## Adding a service

Append to `SERVICES` in `app.py`. `tab: True` gives it a framed pane and an entry in the
rail; without it the service appears in the status strip only, which is right for an API —
a thing with no page in it has nothing to show, and pretending otherwise wastes a tab.

Stdlib only, deliberately: this is a dev console on a box where the model server, the CAD
service and Isaac Sim already compete for memory. It is not worth a framework.
