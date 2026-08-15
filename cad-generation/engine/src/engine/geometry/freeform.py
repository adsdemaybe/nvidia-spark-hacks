"""Run model-written build123d code and turn the result into a measured solid.

This is what makes the platform *text to CAD* rather than text to a choice among five
shapes. Until now a design agent picked a registered generator by name — `tube`, `plate`,
`bracket`, `step_part`, `component` — and passed it params. build123d was doing the real
work underneath, but no model ever wrote any: a part nobody had already parameterised
could not be described at all.

The contract is unchanged, which is the point. `freeform` is a generator like any other:
it returns a `GeometryResult` with a genuine B-rep and mass properties measured by the
same OpenCascade path, so nothing downstream — inertia, URDF export, the co-sim gate —
can tell the difference or needs to.

**Execution model.** The code runs in a subprocess and comes back as STEP.

Not for sandboxing theatre. The realistic failure here is not a malicious payload — the
code comes from our own model on our own box — it is a boolean operation that never
terminates, or a `while True`, or an OCC segfault. All three kill the parent if run
in-process, and all three are survivable in a child: the timeout fires, the exception is
attributed to the stage that caused it, and the design loop reports a bad part instead of
dying. The STEP hand-off is what makes that possible, since an OCC shape cannot be
pickled across the boundary.

The namespace is still restricted, because cheap defence against an accidental
`shutil.rmtree` in generated code is worth having even when nobody is attacking you.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

# What the generated code may use. build123d re-exports its whole API from the package
# root, so importing it wholesale is both what the docs show and what a model will write.
PREAMBLE = """
import math
from build123d import *
"""

# Blocked outright rather than merely absent: `import os` would otherwise still work
# inside the child, and a model that has decided it needs the filesystem will write the
# import rather than give up.
FORBIDDEN = (
    "import os", "import sys", "import shutil", "import subprocess", "import socket",
    "import requests", "import urllib", "__import__", "open(", "eval(", "exec(",
    "compile(", "globals(", "locals(",
)

DEFAULT_TIMEOUT_S = 40

_CHILD = r'''
import json, sys, traceback
{preamble}

_out = {out!r}
try:
    # Seeded from this module's globals, which is where `from build123d import *`
    # landed. Passing a fresh dict instead makes every build123d name a NameError, and
    # the message ("name 'Box' is not defined") points at the generated code rather than
    # at the harness that withheld the import.
    _ns = dict(globals())
    exec(compile({code!r}, "<model-generated>", "exec"), _ns, _ns)
    part = _ns.get("part")
    if part is None:
        raise ValueError(
            "the code did not bind `part`. Assign the finished solid to a variable "
            "named `part`."
        )
    # Builder objects and algebra results both need coercing to a real shape.
    if hasattr(part, "part"):
        part = part.part
    if hasattr(part, "sketch") and part.__class__.__name__ == "BuildSketch":
        raise ValueError("`part` is a 2D sketch, not a solid. Extrude or revolve it.")
    from build123d import export_step
    export_step(part, _out)
    print(json.dumps({{"ok": True}}))
except Exception as exc:
    print(json.dumps({{"ok": False, "error": f"{{type(exc).__name__}}: {{exc}}",
                      "traceback": traceback.format_exc()[-2000:]}}))
'''


class FreeformError(RuntimeError):
    """The generated code did not produce a usable solid.

    Carries the child's traceback: this text goes back to the model as the work order, and
    "it failed" is not something a model can act on while "Edge is not tangent" is.
    """

    def __init__(self, message: str, traceback_text: str = "") -> None:
        super().__init__(message)
        self.traceback_text = traceback_text


def check_source(code: str) -> None:
    lowered = code.lower()
    for bad in FORBIDDEN:
        if bad in lowered:
            raise FreeformError(
                f"generated code uses {bad!r}, which is not available. Build the solid "
                f"from build123d primitives only — no file, network or process access."
            )


def run_to_step(code: str, timeout_s: int = DEFAULT_TIMEOUT_S,
                python: str | None = None) -> Path:
    """Execute `code`, return the path to the STEP it produced.

    The caller owns the returned file's directory and should clean it up.
    """
    check_source(code)
    tmp = Path(tempfile.mkdtemp(prefix="freeform-"))
    out = tmp / "part.step"
    script = _CHILD.format(preamble=PREAMBLE, out=str(out), code=textwrap.dedent(code))
    src = tmp / "run.py"
    src.write_text(script, encoding="utf8")

    try:
        proc = subprocess.run(
            [python or sys.executable, str(src)],
            capture_output=True, text=True, timeout=timeout_s, cwd=str(tmp),
        )
    except subprocess.TimeoutExpired:
        raise FreeformError(
            f"the geometry did not finish in {timeout_s}s. A boolean against a very high "
            f"face count, or an unbounded loop — simplify the solid."
        ) from None

    line = next((l for l in reversed(proc.stdout.splitlines()) if l.startswith("{")), "")
    if not line:
        raise FreeformError(
            f"the child produced no result (exit {proc.returncode}). "
            f"stderr: {proc.stderr[-600:]}"
        )
    result = json.loads(line)
    if not result.get("ok"):
        raise FreeformError(result.get("error", "unknown failure"),
                            result.get("traceback", ""))
    if not out.exists() or out.stat().st_size == 0:
        raise FreeformError("the code ran but exported no geometry")
    return out
