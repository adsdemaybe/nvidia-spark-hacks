"""Direct client for a Cosmos Reason NIM (Backend B — fallback/debug path).

Exists so the feature is testable even when the full VSS profile isn't
deployed (COSMOS_VSS.md §5). Sends the video inline as a base64 data URL to
an OpenAI-compatible `/chat/completions` endpoint, which is the lowest common
denominator across NIM deployments.
"""

from __future__ import annotations

import base64
from pathlib import Path

import httpx


class CosmosClientError(RuntimeError):
    pass


class CosmosNimClient:
    def __init__(self, base_url: str, *, timeout_s: float = 180.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def health_ready(self) -> bool:
        try:
            resp = httpx.get(self._url("/health/ready"), timeout=10.0)
            if resp.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        # Not every NIM exposes /health/ready; a reachable /models is a
        # reasonable second signal that the service is up.
        try:
            resp = httpx.get(self._url("/models"), timeout=10.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def list_models(self) -> list[str]:
        resp = httpx.get(self._url("/models"), timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        return [m.get("id", "") for m in data.get("data", [])]

    def chat_completion(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        video_path: Path,
    ) -> str:
        video_b64 = base64.b64encode(video_path.read_bytes()).decode("ascii")
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "video_url",
                            "video_url": {"url": f"data:video/mp4;base64,{video_b64}"},
                        },
                    ],
                },
            ],
            "max_tokens": 2048,
            "temperature": 0.0,
        }
        resp = httpx.post(self._url("/chat/completions"), json=payload, timeout=self._timeout_s)
        resp.raise_for_status()
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise CosmosClientError(f"unexpected chat completion response shape: {data}") from exc
