"""
Provider layer: one factory, every model.

Agents never import a provider package. They ask for a ROLE ("proposer",
"critic") and get back a configured LangChain chat model, so swapping Claude for
Gemini is a config change, not a code change.

Providers are resolved lazily — an unconfigured provider costs nothing until
something actually asks for it, so a machine with only one API key still runs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any

# Registry: provider key -> (pip package, import path, class name, env var).
PROVIDERS: dict[str, tuple[str, str, str, str]] = {
    "anthropic": ("langchain-anthropic", "langchain_anthropic",
                  "ChatAnthropic", "ANTHROPIC_API_KEY"),
    "openai": ("langchain-openai", "langchain_openai",
               "ChatOpenAI", "OPENAI_API_KEY"),
    "google": ("langchain-google-genai", "langchain_google_genai",
               "ChatGoogleGenerativeAI", "GOOGLE_API_KEY"),
    # Local models need no key; the env var is checked as a host override.
    "ollama": ("langchain-ollama", "langchain_ollama", "ChatOllama",
               "OLLAMA_HOST"),
}


@dataclass(frozen=True)
class ModelSpec:
    """A resolved model choice for one agent role."""
    provider: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 4096

    def with_(self, **kw: Any) -> "ModelSpec":
        return replace(self, **kw)


class ProviderError(RuntimeError):
    pass


def available() -> dict[str, bool]:
    """Which providers are usable right now (package importable + key present)."""
    out = {}
    for key, (_pkg, mod, _cls, env) in PROVIDERS.items():
        try:
            __import__(mod)
            importable = True
        except ImportError:
            importable = False
        # Ollama runs locally, so a key is not required.
        out[key] = importable and (key == "ollama" or bool(os.environ.get(env)))
    return out


def build(spec: ModelSpec, **overrides: Any):
    """Instantiate the LangChain chat model described by `spec`."""
    if spec.provider not in PROVIDERS:
        raise ProviderError(
            f"unknown provider {spec.provider!r}; known: {sorted(PROVIDERS)}")
    pkg, mod, cls_name, env = PROVIDERS[spec.provider]

    try:
        module = __import__(mod, fromlist=[cls_name])
    except ImportError as exc:
        raise ProviderError(
            f"provider {spec.provider!r} needs `pip install {pkg}`") from exc

    if spec.provider != "ollama" and not os.environ.get(env):
        raise ProviderError(
            f"provider {spec.provider!r} needs the {env} environment variable")

    cls = getattr(module, cls_name)
    kwargs: dict[str, Any] = {
        "model": spec.model,
        "temperature": spec.temperature,
        **overrides,
    }
    # Not every provider names the output cap the same way.
    if spec.provider in ("anthropic", "openai"):
        kwargs["max_tokens"] = spec.max_tokens
    elif spec.provider == "google":
        kwargs["max_output_tokens"] = spec.max_tokens

    return cls(**kwargs)


def build_structured(spec: ModelSpec, schema, **overrides: Any):
    """
    A model that must answer with `schema` (a pydantic model).

    Structured output is what makes the harness able to trust an agent's reply:
    a proposal arrives as typed fields it can validate and apply, never as prose
    it would have to parse.
    """
    return build(spec, **overrides).with_structured_output(schema)
