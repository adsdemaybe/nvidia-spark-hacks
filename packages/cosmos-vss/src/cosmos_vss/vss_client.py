"""Thin REST client for the NVIDIA VSS Real-Time VLM service (Backend A).

No retries, no fallback logic, no NGC/docker concerns — those stay in the
separately-deployed VSS service (COSMOS_VSS.md §15). This is deliberately a
dumb pipe over the documented endpoints.
"""

from __future__ import annotations

from pathlib import Path

import httpx


class VssClientError(RuntimeError):
    pass


class VssClient:
    def __init__(self, base_url: str, *, timeout_s: float = 180.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def health_ready(self) -> bool:
        try:
            resp = httpx.get(self._url("/health/ready"), timeout=10.0)
        except httpx.HTTPError:
            return False
        return resp.status_code == 200

    def list_models(self) -> list[str]:
        resp = httpx.get(self._url("/models"), timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        return [m.get("id", "") for m in data.get("data", [])]

    def upload_file(self, path: Path) -> str:
        with path.open("rb") as f:
            resp = httpx.post(
                self._url("/files"),
                files={"file": (path.name, f, "video/mp4")},
                timeout=self._timeout_s,
            )
        resp.raise_for_status()
        data = resp.json()
        file_id = data.get("id") or data.get("file_id")
        if not file_id:
            raise VssClientError(f"upload response missing file id: {data}")
        return file_id

    def generate_captions(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        file_id: str | None = None,
        stream_id: str | None = None,
    ) -> dict:
        if not file_id and not stream_id:
            raise VssClientError("generate_captions requires either file_id or stream_id")

        payload: dict = {
            "model": model,
            "prompt": user_prompt,
            "system_prompt": system_prompt,
        }
        if file_id:
            payload["id"] = file_id
        if stream_id:
            payload["stream_id"] = stream_id

        resp = httpx.post(self._url("/generate_captions"), json=payload, timeout=self._timeout_s)
        resp.raise_for_status()
        return resp.json()

    def add_stream(self, url: str, *, description: str = "") -> str:
        resp = httpx.post(
            self._url("/streams/add"),
            json={"url": url, "description": description},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        stream_id = data.get("id") or data.get("stream_id")
        if not stream_id:
            raise VssClientError(f"add_stream response missing stream id: {data}")
        return stream_id

    def get_stream_info(self, stream_id: str) -> dict:
        resp = httpx.get(self._url("/streams/get-stream-info"), params={"id": stream_id}, timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def delete_stream(self, stream_id: str) -> None:
        resp = httpx.delete(self._url(f"/streams/delete/{stream_id}"), timeout=10.0)
        resp.raise_for_status()
