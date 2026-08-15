"""Gate execution and reporting.

Two deliberate choices:

* `run_gates` never short-circuits. You want the whole picture of what is wrong
  with an artifact in one run, not the first problem alphabetically. (The
  cheap->expensive short-circuit ordering in the plan applies to *cousin
  candidate rejection*, which is a different loop -- see envgen.cousins.)

* A hard failure still writes the artifact to the store, marked FAIL. It is
  simply not promoted into the run manifest. You can then inspect a failure
  without re-running the stage that produced it.
"""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from ..artifacts import Degradation, DegradationLevel
from .exitcodes import ExitCode
from .registry import GateResult, GateSpec, Severity, Verdict, gates_for


class GateReport(BaseModel):
    stage: str
    subject_id: str
    results: list[GateResult] = Field(default_factory=list)

    @property
    def n_pass(self) -> int:
        return sum(r.verdict is Verdict.PASS for r in self.results)

    @property
    def n_warn(self) -> int:
        return sum(r.verdict is Verdict.WARN for r in self.results)

    @property
    def n_fail(self) -> int:
        return sum(r.verdict is Verdict.FAIL for r in self.results)

    @property
    def n_skip(self) -> int:
        return sum(r.verdict is Verdict.SKIP for r in self.results)

    @property
    def blocked(self) -> bool:
        return any(r.blocking for r in self.results)

    @property
    def verdict(self) -> str:
        if self.blocked:
            return "FAIL"
        return "PASS_DEGRADED" if self.n_warn else "PASS"

    @property
    def exit_code(self) -> ExitCode:
        if self.blocked:
            return ExitCode.GATE_FAIL
        return ExitCode.PASS_DEGRADED if self.n_warn else ExitCode.PASS

    def degradations(self) -> list[Degradation]:
        """Soft failures become degradation records on the artifact."""
        return [
            Degradation(
                code=r.gate_id,
                level=DegradationLevel.DEGRADED,
                stage=self.stage,
                message=r.message,
                remediation=None,
            )
            for r in self.results
            if r.verdict is Verdict.WARN
        ]

    def failures(self) -> list[GateResult]:
        return [r for r in self.results if r.verdict in (Verdict.FAIL, Verdict.WARN)]


def _run_one(spec: GateSpec, subject: Any, ctx: Any) -> GateResult:
    t0 = time.perf_counter()
    try:
        res = spec.fn(subject, ctx)
    except Exception as exc:  # a gate that crashes is a failed gate, not a crashed run
        return GateResult(
            gate_id=spec.id,
            verdict=Verdict.FAIL if spec.severity is Severity.HARD else Verdict.WARN,
            severity=spec.severity,
            message=f"gate raised {type(exc).__name__}: {exc}",
            duration_ms=(time.perf_counter() - t0) * 1e3,
        )
    return res.model_copy(update={"duration_ms": (time.perf_counter() - t0) * 1e3})


def run_gates(stage: str, subject: Any, ctx: Any = None, subject_id: str = "") -> GateReport:
    """Run every gate registered for `stage`. Never short-circuits."""
    specs = gates_for(stage)
    results = [_run_one(s, subject, ctx) for s in specs]
    return GateReport(stage=stage, subject_id=subject_id, results=results)


def render(report: GateReport, console=None) -> None:
    """Human-readable table: measured vs threshold, so failures self-explain."""
    from rich.console import Console
    from rich.table import Table

    console = console or Console()
    style = {"PASS": "green", "PASS_DEGRADED": "yellow", "FAIL": "red"}[report.verdict]
    table = Table(
        title=f"[{style}]{report.stage} -> {report.verdict}[/{style}]"
        f"  ({report.n_pass} pass, {report.n_warn} warn, {report.n_fail} fail)",
        header_style="bold",
    )
    table.add_column("gate")
    table.add_column("verdict")
    table.add_column("measured")
    table.add_column("threshold")
    table.add_column("message", overflow="fold")

    mark = {"PASS": "[green]PASS[/green]", "WARN": "[yellow]WARN[/yellow]",
            "FAIL": "[red]FAIL[/red]", "SKIP": "[dim]SKIP[/dim]"}
    for r in report.results:
        table.add_row(
            r.gate_id,
            mark[r.verdict],
            ", ".join(f"{k}={v}" for k, v in r.measured.items()) or "-",
            ", ".join(f"{k}={v}" for k, v in r.thresholds.items()) or "-",
            r.message or "",
        )
    console.print(table)
