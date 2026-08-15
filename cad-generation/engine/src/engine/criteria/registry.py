from __future__ import annotations

from engine.criteria.base import Criterion, CriterionFn

_REGISTRY: dict[str, Criterion] = {}


def register(name: str, *, tier: int):
    def decorator(fn: CriterionFn) -> CriterionFn:
        if name in _REGISTRY:
            raise ValueError(f"criterion {name!r} already registered")
        _REGISTRY[name] = Criterion(name=name, tier=tier, fn=fn)
        return fn

    return decorator


def all_criteria() -> list[Criterion]:
    return sorted(_REGISTRY.values(), key=lambda c: (c.tier, c.name))
