"""Live-stream scaffolding for VSS's RTSP ingestion (COSMOS_VSS.md §14).

This is the optional second path — only wired up after the recorded-video
path is reliable. It wraps `VssClient`'s stream endpoints and normalizes
streamed caption chunks into small, timestamped events; it does not attempt
to build a complete `SemanticEpisode` from a live stream.
"""

from __future__ import annotations

from dataclasses import dataclass

from .vss_client import VssClient


@dataclass(frozen=True)
class StreamEvent:
    stream_id: str
    start_s: float | None
    end_s: float | None
    text: str


class RtspLiveSession:
    """A single VSS stream registration, added/queried/torn down explicitly.

    The RTSP publisher itself (camera -> RTSP) is an external bridge —
    see tools/webcam_to_rtsp.md — not something this package launches.
    """

    def __init__(self, client: VssClient, *, rtsp_url: str, description: str = "") -> None:
        self._client = client
        self._rtsp_url = rtsp_url
        self._description = description
        self._stream_id: str | None = None

    @property
    def stream_id(self) -> str | None:
        return self._stream_id

    def start(self) -> str:
        self._stream_id = self._client.add_stream(self._rtsp_url, description=self._description)
        return self._stream_id

    def info(self) -> dict:
        if self._stream_id is None:
            raise RuntimeError("stream not started")
        return self._client.get_stream_info(self._stream_id)

    def stop(self) -> None:
        if self._stream_id is not None:
            self._client.delete_stream(self._stream_id)
            self._stream_id = None


def normalize_caption_chunk(stream_id: str, chunk: dict) -> StreamEvent:
    return StreamEvent(
        stream_id=stream_id,
        start_s=chunk.get("start_s", chunk.get("start")),
        end_s=chunk.get("end_s", chunk.get("end")),
        text=chunk.get("caption") or chunk.get("text") or chunk.get("result") or "",
    )
