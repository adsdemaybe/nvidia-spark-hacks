"""
Per-role model configuration.

Roles are the unit of configuration, not agents: a role says what kind of
thinking is needed, and the config says which model does it. Cheap critics and
an expensive proposer is the normal shape, and it is one dict to change.

Override anything from the environment without touching code:
    ROVER_PROPOSER=anthropic:claude-opus-4-5
    ROVER_CRITIC=ollama:llama3.1
"""

from __future__ import annotations

import os

from .providers import ModelSpec, available

#: Sensible defaults per role. Latest Claude models by default.
DEFAULTS: dict[str, ModelSpec] = {
    # Diagnoses *why* the design fails — needs the most capable model.
    "analyst": ModelSpec("anthropic", "claude-opus-4-5", temperature=0.0),
    # Generates candidate design changes; a little heat helps it explore.
    "proposer": ModelSpec("anthropic", "claude-opus-4-5", temperature=0.6),
    # Adversarially attacks a proposal. Runs N times, so keep it cheap.
    "critic": ModelSpec("anthropic", "claude-sonnet-5", temperature=0.2),
    # Summarises the converged run for a human.
    "reporter": ModelSpec("anthropic", "claude-sonnet-5", temperature=0.1),
}

#: Fallback order when a role's configured provider has no credentials.
FALLBACK_ORDER = ("anthropic", "openai", "google", "ollama")

FALLBACK_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o-mini",
    "google": "gemini-2.0-flash",
    "ollama": "llama3.1",
}


def _from_env(role: str) -> ModelSpec | None:
    raw = os.environ.get(f"ROVER_{role.upper()}")
    if not raw:
        return None
    provider, _, model = raw.partition(":")
    if not model:
        raise ValueError(f"ROVER_{role.upper()} must be 'provider:model'")
    base = DEFAULTS.get(role, ModelSpec(provider, model))
    return base.with_(provider=provider, model=model)


def spec_for(role: str, *, allow_fallback: bool = True) -> ModelSpec:
    """Resolve the model for a role: env override, default, then fallback."""
    spec = _from_env(role) or DEFAULTS.get(role)
    if spec is None:
        raise KeyError(f"no model configured for role {role!r}")

    if not allow_fallback:
        return spec

    ready = available()
    if ready.get(spec.provider):
        return spec
    for provider in FALLBACK_ORDER:
        if ready.get(provider):
            return spec.with_(provider=provider,
                              model=FALLBACK_MODELS[provider])
    return spec  # nothing configured; let build() raise a clear error


def describe() -> str:
    ready = available()
    lines = ["provider availability:"]
    for k, v in ready.items():
        lines.append(f"  {'ready ' if v else 'absent'}  {k}")
    lines.append("role resolution:")
    for role in DEFAULTS:
        s = spec_for(role)
        lines.append(f"  {role:9} -> {s.provider}:{s.model}  T={s.temperature}")
    return "\n".join(lines)
