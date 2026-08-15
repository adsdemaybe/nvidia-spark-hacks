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

**Reaching through the electronics boundary (§9, v3).** The doc is specific:

    "Coverage perturbation reaches through the boundary: perturbing motor choice
    must move `rail_margin`, or the electronics subsystem is BLIND and the
    integration is decorative."

Two additions serve that, and they are separate functions rather than one,
because a discrete swap is not a +/-10% perturbation and pretending they share a
shape would put a meaningless "relative response to a 10% change" next to a
motor part number:

- `analyze_coverage` now also perturbs the electronics subsystem's own
  continuous variables — rail voltage, budgeted current, harness length and
  gauge. These are design variables like any other and were previously invisible.
- `analyze_catalogue_coverage` substitutes each actuated joint's motor for every
  other entry in its catalogue and measures the response. That is what
  "perturbing motor choice" means for a discrete variable, and it is the check
  that catches a decorative integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.criteria.base import CriterionResult
from engine.evaluate import EvaluationReport, evaluate
from engine.ir import Quantity, RobotIR

_PERTURBATION = 0.10
_BLIND_THRESHOLD = 0.001  # relative response below this counts as no response

# Catalogues whose entries are interchangeable as a joint's actuator. Named
# rather than inferred from the spec type, because "anything that resolves to a
# MotorSpec" would also sweep a gearbox in as a motor.
ACTUATOR_CATALOGUES: tuple[str, ...] = ("stepper_motors", "servos")


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


def _electronics_variables(ir: RobotIR) -> list[tuple[str, str, Quantity]]:
    """Every continuous variable in the electronics subsystem, as (subject, field, q).

    The board envelope is deliberately absent: `max_outline` is a `Vec3` of
    millimetres rather than a `Quantity`, and perturbing it would move
    `board_fits_bay` by construction on any board that has been routed —
    measuring the criterion's arithmetic rather than the design's coverage.
    """
    if ir.electronics is None:
        return []
    out: list[tuple[str, str, Quantity]] = []
    for rail in ir.electronics.rails:
        out.append((f"rail:{rail.id}", "voltage", rail.voltage))
        out.append((f"rail:{rail.id}", "budget_current", rail.budget_current))
        if rail.source_resistance is not None:
            out.append((f"rail:{rail.id}", "source_resistance", rail.source_resistance))
    for harness in ir.electronics.harnesses:
        out.append((f"harness:{harness.id}", "length", harness.length))
        out.append((f"harness:{harness.id}", "conductor_area", harness.conductor_area))
    return out


def _perturbed_electronics(ir: RobotIR, subject: str, field_name: str, factor: float) -> RobotIR:
    kind, ident = subject.split(":", 1)
    clone = ir.model_copy(deep=True)
    assert clone.electronics is not None
    target = (
        clone.electronics.rail(ident)
        if kind == "rail"
        else next(h for h in clone.electronics.harnesses if h.id == ident)
    )
    original: Quantity = getattr(target, field_name)
    setattr(target, field_name, original.model_copy(update={"value": original.value * factor}))
    # Round-trip through validation: a perturbation that produces an IR the
    # schema rejects is a perturbation whose result nobody should be measuring.
    return RobotIR.model_validate(clone.model_dump())


def _response(
    baseline_results: dict[str, CriterionResult],
    perturbed: dict[str, CriterionResult],
    responses: dict[str, float],
    fragile: list[str],
) -> None:
    for name, base_result in baseline_results.items():
        if name not in perturbed:
            continue
        denom = abs(base_result.magnitude) if base_result.magnitude != 0 else 1.0
        relative = abs(perturbed[name].magnitude - base_result.magnitude) / denom
        responses[name] = max(responses.get(name, 0.0), relative)
        if base_result.passed and not perturbed[name].passed and name not in fragile:
            fragile.append(name)


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
                _response(baseline_results, perturbed, responses, fragile)

            # No responses at all is the *most* blind a variable can be, not the
            # least: `bool(responses) and ...` reported the one case where nothing
            # measured the variable as covered.
            blind = all(r < _BLIND_THRESHOLD for r in responses.values())
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

    # The electronics subsystem's own variables. Absent before v3, which meant a
    # rail voltage could be anything at all and no coverage report would notice.
    for subject, field_name, quantity in _electronics_variables(ir):
        responses = {}
        fragile = []
        for factor in (1.0 + _PERTURBATION, 1.0 - _PERTURBATION):
            perturbed_ir = _perturbed_electronics(ir, subject, field_name, factor)
            perturbed = _by_name(evaluate(perturbed_ir, max_tier=max_tier))
            _response(baseline_results, perturbed, responses, fragile)
        coverage.append(
            VariableCoverage(
                link_id=subject,
                param_name=field_name,
                baseline_value=quantity.value,
                responses=responses,
                blind=all(r < _BLIND_THRESHOLD for r in responses.values()),
                fragile_criteria=fragile,
            )
        )

    return coverage


@dataclass(frozen=True)
class CatalogueCoverage:
    """Response to swapping a discrete part, not to nudging a continuous one.

    A separate shape from `VariableCoverage` on purpose. "Relative response to a
    +/-10% change" is meaningless next to a motor part number, and reporting one
    anyway would put a number in a coverage matrix that no reader could act on.
    """

    subject: str  # "joint:bracket_to_wheel"
    catalogue: str
    baseline_key: str
    # criterion name -> largest relative change seen across the alternatives
    responses: dict[str, float] = field(default_factory=dict)
    alternatives_tried: tuple[str, ...] = ()
    blind: bool = True


def analyze_catalogue_coverage(
    ir: RobotIR, *, max_tier: int = 0, limit: int = 8
) -> list[CatalogueCoverage]:
    """Swap each actuator for its catalogue's alternatives and measure the response.

    §9's specific test of whether the electronics integration is real: if
    changing the motor does not move `rail_margin`, the two halves of the
    platform are only nominally connected.

    Alternatives that will not evaluate are skipped rather than scored — a
    catalogue entry that produces an invalid IR is a catalogue bug, and counting
    it as "no response" would report a *blind* variable where the truth is an
    unevaluable one.
    """
    from engine.catalogue import CATALOGUES

    baseline_results = _by_name(evaluate(ir, max_tier=max_tier))
    out: list[CatalogueCoverage] = []

    for joint in ir.joints:
        if joint.actuator is None:
            continue
        if joint.actuator.catalogue not in ACTUATOR_CATALOGUES:
            continue

        # Across every actuator catalogue, not just the one the joint currently
        # uses. A motor is a motor whether it is filed under `servos` or
        # `stepper_motors`, and searching only within one catalogue was hiding a
        # real blindness: all three `stepper_motors` entries share a 1.7 A rated
        # current, so swapping among them moves no electrical criterion at all
        # and `rail_margin` looked BLIND when it is not.
        alternatives: list[tuple[str, str]] = []
        for name in ACTUATOR_CATALOGUES:
            catalogue = CATALOGUES.get(name)
            if catalogue is None:
                continue
            alternatives += [
                (name, k)
                for k in catalogue.keys()
                if not (name == joint.actuator.catalogue and k == joint.actuator.value)
            ]
        alternatives = alternatives[:limit]

        responses: dict[str, float] = {}
        tried: list[str] = []
        for catalogue_name, key in alternatives:
            data = ir.model_dump()
            target = next(j for j in data["joints"] if j["id"] == joint.id)
            target["actuator"]["value"] = key
            target["actuator"]["catalogue"] = catalogue_name
            try:
                swapped = RobotIR.model_validate(data)
                perturbed = _by_name(evaluate(swapped, max_tier=max_tier))
            except Exception:
                continue
            tried.append(f"{catalogue_name}/{key}")
            _response(baseline_results, perturbed, responses, [])

        out.append(
            CatalogueCoverage(
                subject=f"joint:{joint.id}",
                catalogue=joint.actuator.catalogue,
                baseline_key=joint.actuator.value,
                responses=responses,
                alternatives_tried=tuple(tried),
                blind=all(r < _BLIND_THRESHOLD for r in responses.values()),
            )
        )
    return out
