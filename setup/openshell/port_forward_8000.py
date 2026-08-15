"""Forward 127.0.0.1:8000 -> 127.0.0.1:8100.

nemoclaw's `vllm-local` provider has port 8000 baked in: `nemoclaw inference set` refuses
to update a sandbox's record until something answers there ("Local vLLM was selected, but
nothing is responding on http://127.0.0.1:8000"), and `--no-verify` does not skip that
particular probe. Our vLLM serves on 8100, because 8000 was taken when it was first
started and pcb-ai's endpoint table has referenced 8100 ever since.

The alternatives were worse. Moving vLLM to 8000 means restarting the model server every
other service is currently using and editing every reference to 8100. Hand-editing
`~/.nemoclaw/sandboxes.json` means writing state that nemoclaw believes it owns, which
survives until the next thing that rewrites it and then silently disagrees.

So: eleven lines of socket plumbing, no dependencies (socat is not installed here), and
nothing else on the box changes. Run it while onboarding or reconfiguring, stop it after.

    python setup/openshell/port_forward_8000.py &
"""

from __future__ import annotations

import socket
import sys
import threading

LISTEN = ("127.0.0.1", 8000)
TARGET = ("127.0.0.1", 8100)


def pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while chunk := src.recv(65536):
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        # Half-close rather than close: the other direction may still be streaming, and a
        # hard close here truncates a response mid-body.
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def handle(client: socket.socket) -> None:
    try:
        upstream = socket.create_connection(TARGET, timeout=30)
    except OSError as exc:
        print(f"upstream {TARGET[0]}:{TARGET[1]} unreachable: {exc}", file=sys.stderr)
        client.close()
        return
    with client, upstream:
        a = threading.Thread(target=pipe, args=(client, upstream), daemon=True)
        b = threading.Thread(target=pipe, args=(upstream, client), daemon=True)
        a.start()
        b.start()
        a.join()
        b.join()


def main() -> int:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(LISTEN)
    except OSError as exc:
        print(f"cannot bind {LISTEN[0]}:{LISTEN[1]}: {exc}", file=sys.stderr)
        return 1
    srv.listen(64)
    print(f"forwarding {LISTEN[0]}:{LISTEN[1]} -> {TARGET[0]}:{TARGET[1]}", flush=True)
    while True:
        client, _ = srv.accept()
        threading.Thread(target=handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main())
