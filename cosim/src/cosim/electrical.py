"""The electrical participant — duty and shaft speed in, torque out.

Two modes, because the obvious one is too slow to close the loop with:

- **`direct`** calls ngspice once per control period. Exact, and the reference every
  other mode is judged against. Each call costs a process spawn (~20–50 ms) before the
  solver does anything, so a 1000-period rollout spends about a minute in `fork` alone.
  The transport was never the bottleneck; the subprocess is.
- **`surface`** characterises the drive once over a (duty × ω) grid and interpolates at
  run time. The board does not change during a rollout, so re-solving the same circuit
  thousands of times is work with no information in it.

**`surface` is only trustworthy because it is checked against `direct`** — `validate()`
runs both on the same points and reports the error. A fast model nobody compared to the
slow one is a guess wearing a lab coat.

The physics itself lives in `pcb-ai/src/spice/`; this module shells out to it rather than
reimplementing, so there is one motor model and one deck builder in the project.
"""

from __future__ import annotations

import json
import subprocess
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path

def _find_pcb_ai() -> Path:
    """Locate pcb-ai by walking up, not by counting parents.

    Counting `parents[n]` breaks the moment the package is installed elsewhere, moved a
    directory deeper, or vendored — and it breaks with a path that looks almost right,
    which is the worst kind. Searching for the marker file says what it actually needs.
    """
    for base in Path(__file__).resolve().parents:
        candidate = base / "pcb-ai"
        if (candidate / "tools" / "motor-sim.ts").exists():
            return candidate
    return Path(__file__).resolve().parents[3] / "pcb-ai"  # best guess, for the error message


PCB_AI = _find_pcb_ai()
REPO_ROOT = PCB_AI.parent


@dataclass
class OperatingPoint:
    """One evaluated (duty, ω) point."""

    duty: float
    omega_rad_s: float
    current_avg_a: float
    current_peak_a: float
    torque_nm: float
    voltage_v: float
    rail_sag_mv: float


class SpiceBackend:
    """Runs `pcb-ai`'s transient engine as a subprocess.

    Deliberately shelling out rather than reimplementing the deck in Python. The deck
    builder, the motor catalogue and the freewheel path all live in one place, and a fix
    there is a fix here. The cost is a process spawn, which is exactly why `surface`
    exists.
    """

    def __init__(self, motor: str = "n20-6v", freq_hz: float = 20000.0, out_dir: str = "runs/cosim"):
        self.motor = motor
        self.freq_hz = freq_hz
        self.out_dir = out_dir
        if not (PCB_AI / "tools" / "motor-sim.ts").exists():
            raise FileNotFoundError(
                f"cannot find pcb-ai's transient engine at {PCB_AI}. "
                "The electrical participant runs it as a subprocess."
            )

    def evaluate(self, duty: float, omega_rad_s: float) -> OperatingPoint:
        proc = subprocess.run(
            [
                "npx",
                "tsx",
                "tools/motor-sim.ts",
                "--motor", self.motor,
                "--duty", f"{duty:.6f}",
                "--omega", f"{omega_rad_s:.6f}",
                "--freq", f"{self.freq_hz:.0f}",
                "--out", self.out_dir,
                "--json",
            ],
            cwd=PCB_AI,
            capture_output=True,
            text=True,
            timeout=300,
        )
        line = next(
            (l for l in reversed(proc.stdout.splitlines()) if l.strip().startswith("{")),
            None,
        )
        if line is None:
            raise RuntimeError(
                "the transient engine produced no JSON.\n"
                f"stdout tail: {proc.stdout[-400:]}\nstderr tail: {proc.stderr[-400:]}"
            )
        d = json.loads(line)
        return OperatingPoint(
            duty=duty,
            omega_rad_s=omega_rad_s,
            current_avg_a=d["current_avg_a"],
            current_peak_a=d["current_peak_a"],
            torque_nm=d["torque_avg_nm"],
            voltage_v=d["motor_voltage_avg_v"],
            rail_sag_mv=d["supply_sag_peak_mv"],
        )


class Surface:
    """A characterised (duty × ω) map with bilinear interpolation.

    Bilinear rather than anything cleverer because the underlying relationship is close
    to linear in both axes — current rises with duty and falls with back-EMF — so a
    higher-order fit would buy accuracy the grid spacing does not support, and would
    invent oscillations between points.
    """

    def __init__(self, duties: list[float], omegas: list[float], points: list[list[OperatingPoint]]):
        self.duties = duties
        self.omegas = omegas
        self.points = points  # [duty_index][omega_index]

    @classmethod
    def characterise(
        cls,
        backend: SpiceBackend,
        duties: list[float],
        omegas: list[float],
        *,
        progress: bool = True,
    ) -> "Surface":
        grid: list[list[OperatingPoint]] = []
        total = len(duties) * len(omegas)
        n = 0
        for d in duties:
            row = []
            for w in omegas:
                row.append(backend.evaluate(d, w))
                n += 1
                if progress:
                    print(f"  characterising {n}/{total}  duty={d:.2f} ω={w:.0f}", flush=True)
            grid.append(row)
        return cls(duties, omegas, grid)

    def _bracket(self, values: list[float], x: float) -> tuple[int, int, float]:
        """Indices either side of x, and the fraction between them.

        Clamped at both ends: a rollout that briefly exceeds the characterised range
        should saturate at the edge rather than extrapolate into nonsense.
        """
        if x <= values[0]:
            return 0, 0, 0.0
        if x >= values[-1]:
            return len(values) - 1, len(values) - 1, 0.0
        hi = bisect_left(values, x)
        lo = hi - 1
        span = values[hi] - values[lo]
        return lo, hi, (x - values[lo]) / span if span else 0.0

    def evaluate(self, duty: float, omega_rad_s: float) -> OperatingPoint:
        di, dj, df = self._bracket(self.duties, duty)
        wi, wj, wf = self._bracket(self.omegas, omega_rad_s)

        def lerp2(attr: str) -> float:
            a = getattr(self.points[di][wi], attr)
            b = getattr(self.points[di][wj], attr)
            c = getattr(self.points[dj][wi], attr)
            d_ = getattr(self.points[dj][wj], attr)
            top = a + (b - a) * wf
            bot = c + (d_ - c) * wf
            return top + (bot - top) * df

        return OperatingPoint(
            duty=duty,
            omega_rad_s=omega_rad_s,
            current_avg_a=lerp2("current_avg_a"),
            current_peak_a=lerp2("current_peak_a"),
            torque_nm=lerp2("torque_nm"),
            voltage_v=lerp2("voltage_v"),
            rail_sag_mv=lerp2("rail_sag_mv"),
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "duties": self.duties,
                "omegas": self.omegas,
                "points": [[p.__dict__ for p in row] for row in self.points],
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> "Surface":
        d = json.loads(text)
        return cls(
            d["duties"],
            d["omegas"],
            [[OperatingPoint(**p) for p in row] for row in d["points"]],
        )

    @property
    def full_scale_torque_nm(self) -> float:
        """The largest torque anywhere on the grid — the natural yardstick."""
        return max(p.torque_nm for row in self.points for p in row)

    def validate(self, backend: SpiceBackend, samples: list[tuple[float, float]]) -> dict:
        """Compare interpolation against the real solver at points off the grid.

        Off-grid on purpose: testing at grid nodes would only prove the table was stored
        correctly, which is not the question. The question is what happens *between* the
        nodes.

        **Judged against full scale, not against each point's own value.** Relative error
        on a near-zero quantity is a number that looks alarming and means nothing: at
        6% duty this drive makes 0.021 mN·m, so being wrong by 0.12 mN·m is a 592%
        relative error and an irrelevant absolute one, on a motor that produces 8.3 mN·m
        at full drive. What actually matters to a trajectory is the error as a fraction
        of the torque the motor can produce, so that is the acceptance criterion. Both
        are reported, because the relative figure still tells you *where* the surface is
        soft — near the conduction threshold, where the freewheel diode drop puts a kink
        that no amount of grid density removes.
        """
        full_scale = max(self.full_scale_torque_nm, 1e-12)
        errors = []
        for duty, omega in samples:
            exact = backend.evaluate(duty, omega)
            approx = self.evaluate(duty, omega)
            abs_err = abs(approx.torque_nm - exact.torque_nm)
            errors.append(
                {
                    "duty": duty,
                    "omega": omega,
                    "exact_torque_nm": exact.torque_nm,
                    "surface_torque_nm": approx.torque_nm,
                    "abs_error_nm": abs_err,
                    "rel_error": abs_err / max(abs(exact.torque_nm), 1e-12),
                    "full_scale_error": abs_err / full_scale,
                }
            )
        worst_rel = max(errors, key=lambda e: e["rel_error"]) if errors else None
        worst_fs = max(errors, key=lambda e: e["full_scale_error"]) if errors else None
        return {
            "samples": errors,
            "full_scale_torque_nm": full_scale,
            "max_full_scale_error": worst_fs["full_scale_error"] if worst_fs else 0.0,
            "max_rel_error": worst_rel["rel_error"] if worst_rel else 0.0,
            "worst_at": {"duty": worst_fs["duty"], "omega": worst_fs["omega"]} if worst_fs else None,
        }
