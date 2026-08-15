"""Gate registry + runner contracts (r2s.core.gates).

Throwaway gates are registered at import under stages namespaced "__test__*" so
they never collide with real pipeline gates. The registry is process-global, so
each scenario gets its own stage name.
"""
from __future__ import annotations

import pytest

from r2s.core.gates.exitcodes import ExitCode
from r2s.core.gates.registry import (
    GateResult,
    Severity,
    Verdict,
    fail,
    gate,
    gates_for,
    get_gate,
    ok,
)
from r2s.core.gates.runner import run_gates


# ---- throwaway gates ------------------------------------------------------

@gate(
    id="__test__.always_pass",
    stage="__test__hard",
    severity=Severity.HARD,
    describe="always passes",
    remediation="n/a",
)
def _g_pass(subject, ctx):
    spec = get_gate("__test__.always_pass")
    return ok("__test__.always_pass", spec, {"n": 1}, {"n_min": 0}, "fine")


@gate(
    id="__test__.hard_fail",
    stage="__test__hard",
    severity=Severity.HARD,
    describe="always fails hard",
    remediation="fix the subject",
)
def _g_hard_fail(subject, ctx):
    spec = get_gate("__test__.hard_fail")
    return fail("__test__.hard_fail", spec, {"n": 0}, {"n_min": 1}, "too few")


@gate(
    id="__test__.soft_fail",
    stage="__test__soft",
    severity=Severity.SOFT,
    describe="always fails softly",
    remediation="n/a",
)
def _g_soft_fail(subject, ctx):
    spec = get_gate("__test__.soft_fail")
    return fail("__test__.soft_fail", spec, {"q": 0.2}, {"q_min": 0.5}, "quality low")


@gate(
    id="__test__.soft_pass",
    stage="__test__soft",
    severity=Severity.SOFT,
    describe="always passes",
    remediation="n/a",
)
def _g_soft_pass(subject, ctx):
    spec = get_gate("__test__.soft_pass")
    return ok("__test__.soft_pass", spec, {}, {})


@gate(
    id="__test__.raises_hard",
    stage="__test__raise",
    severity=Severity.HARD,
    describe="always raises",
    remediation="n/a",
)
def _g_raises(subject, ctx):
    raise RuntimeError("kaboom inside the gate")


@gate(
    id="__test__.survives",
    stage="__test__raise",
    severity=Severity.HARD,
    describe="passes, proving the run continued past the crash",
    remediation="n/a",
)
def _g_survives(subject, ctx):
    spec = get_gate("__test__.survives")
    return ok("__test__.survives", spec, {}, {})


@gate(
    id="__test__.raises_soft",
    stage="__test__raise_soft",
    severity=Severity.SOFT,
    describe="always raises, softly",
    remediation="n/a",
)
def _g_raises_soft(subject, ctx):
    raise ValueError("soft kaboom")


@gate(
    id="__test__.all_green_a",
    stage="__test__clean",
    severity=Severity.HARD,
    describe="passes",
    remediation="n/a",
)
def _g_green(subject, ctx):
    spec = get_gate("__test__.all_green_a")
    return ok("__test__.all_green_a", spec, {}, {})


# ---- registry -------------------------------------------------------------

class TestRegistry:
    def test_gates_registered_and_scoped_to_stage(self):
        ids = [g.id for g in gates_for("__test__hard")]
        assert ids == ["__test__.always_pass", "__test__.hard_fail"]  # sorted by id

    def test_duplicate_id_rejected(self):
        with pytest.raises(ValueError, match="duplicate gate id"):
            gate(
                id="__test__.hard_fail",
                stage="__test__other",
                severity=Severity.HARD,
                describe="dup",
                remediation="n/a",
            )(lambda s, c: None)

    def test_get_gate(self):
        spec = get_gate("__test__.hard_fail")
        assert spec is not None
        assert spec.severity is Severity.HARD
        assert get_gate("__test__.does_not_exist") is None


# ---- runner ---------------------------------------------------------------

class TestRunner:
    def test_hard_fail_blocks(self):
        report = run_gates("__test__hard", subject=object(), subject_id="art1")
        assert report.blocked
        assert report.verdict == "FAIL"
        assert report.exit_code is ExitCode.GATE_FAIL
        assert int(report.exit_code) == 20
        assert report.n_fail == 1
        assert report.n_pass == 1  # the passing gate still ran: no short-circuit

    def test_soft_fail_degrades_but_passes(self):
        report = run_gates("__test__soft", subject=object(), subject_id="art2")
        assert not report.blocked
        assert report.verdict == "PASS_DEGRADED"
        assert report.exit_code is ExitCode.PASS_DEGRADED
        assert int(report.exit_code) == 10
        assert report.n_warn == 1
        degs = report.degradations()
        assert len(degs) == 1
        assert degs[0].code == "__test__.soft_fail"
        assert degs[0].message == "quality low"

    def test_soft_fail_is_warn_verdict_not_fail(self):
        report = run_gates("__test__soft", subject=object())
        by_id = {r.gate_id: r for r in report.results}
        assert by_id["__test__.soft_fail"].verdict is Verdict.WARN
        assert not by_id["__test__.soft_fail"].blocking

    def test_all_pass(self):
        report = run_gates("__test__clean", subject=object())
        assert report.verdict == "PASS"
        assert report.exit_code is ExitCode.PASS
        assert int(report.exit_code) == 0
        assert report.degradations() == []

    def test_raising_hard_gate_is_a_failed_gate_not_a_crash(self):
        report = run_gates("__test__raise", subject=object())  # must not raise
        by_id = {r.gate_id: r for r in report.results}
        crashed = by_id["__test__.raises_hard"]
        assert crashed.verdict is Verdict.FAIL
        assert "gate raised RuntimeError" in crashed.message
        assert "kaboom inside the gate" in crashed.message
        assert report.blocked  # HARD crash blocks
        # the OTHER gate in the stage still ran and passed
        assert by_id["__test__.survives"].verdict is Verdict.PASS

    def test_raising_soft_gate_is_a_warn(self):
        report = run_gates("__test__raise_soft", subject=object())
        assert not report.blocked
        assert report.n_warn == 1
        assert report.verdict == "PASS_DEGRADED"
        assert "gate raised ValueError" in report.results[0].message

    def test_unknown_stage_is_empty_pass(self):
        report = run_gates("__test__no_such_stage", subject=object())
        assert report.results == []
        assert report.verdict == "PASS"

    def test_result_carries_measured_and_thresholds(self):
        report = run_gates("__test__hard", subject=object())
        failed = [r for r in report.results if r.verdict is Verdict.FAIL][0]
        assert failed.measured == {"n": 0}
        assert failed.thresholds == {"n_min": 1}

    def test_failures_lists_fail_and_warn(self):
        report = run_gates("__test__soft", subject=object())
        assert [r.gate_id for r in report.failures()] == ["__test__.soft_fail"]


class TestGateResultShape:
    def test_blocking_only_for_hard_fail(self):
        hard = GateResult(gate_id="x", verdict=Verdict.FAIL, severity=Severity.HARD)
        soft = GateResult(gate_id="x", verdict=Verdict.WARN, severity=Severity.SOFT)
        info = GateResult(gate_id="x", verdict=Verdict.FAIL, severity=Severity.INFO)
        assert hard.blocking
        assert not soft.blocking
        assert not info.blocking
