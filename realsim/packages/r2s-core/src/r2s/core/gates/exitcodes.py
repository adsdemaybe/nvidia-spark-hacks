"""Process exit codes. `run-all` exits with the max code observed."""
from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    PASS = 0
    PASS_DEGRADED = 10  # soft failures only; artifact usable and labeled
    GATE_FAIL = 20      # a hard gate failed
    MISSING_INPUT = 30  # upstream artifact absent or itself FAIL
    ENV_FAIL = 40       # tool/binary/CUDA missing -- `r2s doctor` says what
    INTERNAL = 50


EXIT_MEANING = {
    ExitCode.PASS: "all gates passed",
    ExitCode.PASS_DEGRADED: "passed with degradations (soft gate failures)",
    ExitCode.GATE_FAIL: "a hard gate failed",
    ExitCode.MISSING_INPUT: "a required upstream artifact is missing or failed",
    ExitCode.ENV_FAIL: "environment/dependency problem -- run `r2s doctor`",
    ExitCode.INTERNAL: "internal error",
}
