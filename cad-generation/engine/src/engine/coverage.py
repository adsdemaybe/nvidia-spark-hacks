"""Coverage analysis (§8): perturb every design variable +/-10%, re-evaluate,
measure each criterion's relative response. Real coverage measures 3-16%; an
unmeasured variable measures 0.0-0.1% — two orders of magnitude apart, so the
BLIND threshold below isn't delicate.

BLIND = no criterion responds at all to a variable (the subsystem is
invisible to the harness — a bug in the harness, not the design; no amount
of search fixes it). FRAGILE = a passing criterion flips to fail under a
+/-10% perturbation (no engineering margin).

Only Quantity-typed geometry params are perturbable — CatalogueParam values
are discrete purchasable-part keys, not continuous variables (§2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.criteria.base import CriterionResult
from engine.evaluate import EvaluationReport, evaluate
from engine.ir import Quantity, RobotIR

_PERTURBATION = 0.10
_BLIND_THRESHOLD = 0.001  # relative response below this counts as no response


@dataclass(frozen=True)
class VariableCoverage:
    link_id: str
    param_name: str
    baseline_value: float
    responses: dict[str, float]  # criterion name -> max relative response across +/-10%
    blind: bool
    fragile_criteria: list[str] = field(default_factory=list)


def _perturbed_ir(ir: RobotIR, link_id: str, param_name: str, factor: float) -> RobotIR:
    clone = ir.model_copy(deep=True)
    link = clone.link(link_id)
    original = link.geometry.params[param_name]
    if not isinstance(original, Quantity):
        raise TypeError(f"cannot perturb non-Quantity param {param_name!r} on link {link_id!r}")
    link.geometry.params[param_name] = original.model_copy(update={"value": original.value * factor})
    return clone


def _by_name(report: EvaluationReport) -> dict[str, CriterionResult]:
    return {r.name: r for r in report.results}


def analyze_coverage(ir: RobotIR, *, max_tier: int = 0) -> list[VariableCoverage]:
    baseline = evaluate(ir, max_tier=max_tier)
    baseline_results = _by_name(baseline)

    coverage: list[VariableCoverage] = []
    for link in ir.links:
        for param_name, param in link.geometry.params.items():
            if not isinstance(param, Quantity):
                continue

            responses: dict[str, float] = {}
            fragile: list[str] = []
            for factor in (1.0 + _PERTURBATION, 1.0 - _PERTURBATION):
                perturbed_ir = _perturbed_ir(ir, link.id, param_name, factor)
                perturbed = _by_name(evaluate(perturbed_ir, max_tier=max_tier))

                for name, base_result in baseline_results.items():
                    if name not in perturbed:
                        continue
                    denom = abs(base_result.magnitude) if base_result.magnitude != 0 else 1.0
                    relative = abs(perturbed[name].magnitude - base_result.magnitude) / denom
                    responses[name] = max(responses.get(name, 0.0), relative)
                    if base_result.passed and not perturbed[name].passed and name not in fragile:
                        fragile.append(name)

            blind = bool(responses) and all(r < _BLIND_THRESHOLD for r in responses.values())
            coverage.append(
                VariableCoverage(
                    link_id=link.id,
                    param_name=param_name,
                    baseline_value=param.value,
                    responses=responses,
                    blind=blind,
                    fragile_criteria=fragile,
                )
            )
    return coverage
