# Bridging a webcam / OBS feed to RTSP for VSS live ingestion

This is external tooling, launched independently of both the AR/VR collector and the
`cosmos-vss` package (COSMOS_VSS.md §14). It exists only to feed VSS's live-stream
endpoints (`/v1/streams/add`, `/v1/streams/get-stream-info`, `/v1/streams/delete/{id}`)
during the optional live-demo path. The recorded-video path (`cosmos-vss analyze demo.mp4`)
does not need any of this.

## 1. Run an RTSP media server

Use an off-the-shelf server rather than hand-rolling one. [MediaMTX](https://github.com/bluenviron/mediamtx)
is a single static binary with no config required for the default case:

```bash
# on the machine that will host the RTSP endpoint (can be the Spark box itself,
# or any machine VSS can reach)
./mediamtx
# listens on rtsp://<host>:8554/<path> by default
```

## 2. Publish the webcam (or OBS output) into it with FFmpeg

macOS / Linux webcam:

```bash
ffmpeg -f v4l2 -i /dev/video0 \
  -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p \
  -f rtsp rtsp://<mediamtx-host>:8554/demo
```

Windows webcam (DirectShow):

```bash
ffmpeg -f dshow -i video="Your Camera Name" \
  -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p \
  -f rtsp rtsp://<mediamtx-host>:8554/demo
```

OBS: Settings -> Stream -> Custom -> Server `rtsp://<mediamtx-host>:8554`, Stream Key `demo`
(or use OBS's built-in "Start Virtual Camera" plus the FFmpeg command above).

Phone camera: use any RTSP-capable camera app (e.g. "IP Webcam" on Android) that publishes
directly to `rtsp://<mediamtx-host>:8554/demo`, or capture to a local file and use the
recorded-video path instead — it is simpler and is the required demo path anyway.

## 3. Point VSS at the RTSP URL

```bash
curl -X POST http://<spark-host>:8000/v1/streams/add \
  -H 'Content-Type: application/json' \
  -d '{"url": "rtsp://<mediamtx-host>:8554/demo", "description": "cosmos-vss live demo"}'
```

or use `cosmos_vss.rtsp.RtspLiveSession` from Python:

```python
from cosmos_vss.config import Config
from cosmos_vss.vss_client import VssClient
from cosmos_vss.rtsp import RtspLiveSession

config = Config.from_env()
session = RtspLiveSession(
    VssClient(config.vss_base_url, timeout_s=config.timeout_s),
    rtsp_url="rtsp://<mediamtx-host>:8554/demo",
)
stream_id = session.start()
print(session.info())
# ...
session.stop()
```

## Notes

- MediaMTX and FFmpeg are not vendored into this repository (COSMOS_VSS.md §15) — install
  them separately on whichever machine does the publishing.
- Prefer the recorded-video path for the actual hackathon demo; live RTSP is Phase 8, an
  enhancement layered on top once the recorded path is reliable.
