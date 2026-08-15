# systemd units

```bash
mkdir -p ~/.config/systemd/user
cp setup/systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now struct-console
loginctl enable-linger "$USER"      # so it survives logout
```

## struct-console

The pipeline front end on **:8600**. `Restart=always`, so a crash or an OOM kill comes
back on its own; with lingering enabled it also survives logout and starts at boot.

Two things in the unit that are not boilerplate:

**`Environment=PATH=…` including `~/.local/bin`.** The console shells out to `npx tsx`
for design runs. A systemd user service does not inherit a login shell's PATH, so without
this it starts perfectly and then every run fails at `cannot run npx` — which reads like a
pipeline fault and is a unit-file one.

**`--host 0.0.0.0`.** Deliberate, and worth understanding before you widen it further.

## Who can reach it

| from | address |
|---|---|
| the box | `http://127.0.0.1:8600` |
| the LAN | `http://172.16.94.156:8600` |
| Tailscale | `http://100.82.201.40:8600` |

**Do not put this behind a public tunnel as it stands.** The Design view accepts a
specification and runs `npx tsx src/cli.ts` with it — anyone who can reach the port can
start subprocesses on this box. On a LAN or a Tailscale network that is a dev tool; on the
open internet it is remote code execution with a text box in front of it.

If it needs to be shared beyond the tailnet, the shape that is safe is a read-only
deployment: serve `/api/overview` and the Overview view, and drop `/run` and `/stream`.
The overview is pure reads of files already on disk and exposes nothing that can execute.

    journalctl --user -u struct-console -f     # logs
    systemctl --user restart struct-console    # after editing app.py
