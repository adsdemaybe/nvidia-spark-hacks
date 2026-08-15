/**
 * The physical world, as pixels (AR/XR plan §26, §41, §47).
 *
 * TWIN's first layer is "Real Environment: the camera". Without it the client
 * renders a robot on a black void, which §66 is explicit about: that is AR
 * visualization, not a digital twin.
 *
 * Two ways the real world gets in, and they are NOT equivalent:
 *
 *   webcam (this file)  -> pixels only. No pose, no planes, no scale. The
 *                          virtual scene floats over the video and slides off
 *                          the moment the camera moves.
 *   WebXR immersive-ar  -> pixels AND 6-DoF tracking (xr.ts). Virtual content
 *                          stays put in the room. This is what §48/§49 need.
 *
 * The webcam path exists because it works on any laptop today and makes the
 * overlay legible to a human. It must always be labelled as untracked -- §82's
 * rule about never presenting a stand-in as the real thing applies here just as
 * much as it does to fixture twin state.
 */

import * as THREE from "three";

export interface CameraFeed {
  texture: THREE.VideoTexture;
  video: HTMLVideoElement;
  stream: MediaStream;
  label: string;
  /** Native aspect of the feed, for letterboxing without distortion. */
  aspect: number;
  stop(): void;
}

export class CameraUnavailable extends Error {}

export async function listCameras(): Promise<MediaDeviceInfo[]> {
  if (!navigator.mediaDevices?.enumerateDevices) return [];
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices.filter((d) => d.kind === "videoinput");
}

/**
 * Start the rear-facing camera where there is one, otherwise whatever exists.
 *
 * `facingMode: environment` is an *ideal* constraint, not exact: on a laptop
 * there is no rear camera and an exact constraint would fail outright rather
 * than fall back to the one webcam that is present.
 */
export async function startCamera(deviceId?: string): Promise<CameraFeed> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new CameraUnavailable(
      "getUserMedia unavailable — needs a secure context (https or localhost)",
    );
  }

  const video: MediaTrackConstraints = deviceId
    ? { deviceId: { exact: deviceId } }
    : { facingMode: { ideal: "environment" } };

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
  } catch (error) {
    throw new CameraUnavailable(`camera refused: ${String(error)}`);
  }

  const element = document.createElement("video");
  element.srcObject = stream;
  element.playsInline = true;
  element.muted = true;
  await element.play();

  const track = stream.getVideoTracks()[0];
  const settings = track?.getSettings() ?? {};
  const width = settings.width ?? element.videoWidth ?? 1280;
  const height = settings.height ?? element.videoHeight ?? 720;

  const texture = new THREE.VideoTexture(element);
  texture.colorSpace = THREE.SRGBColorSpace;

  return {
    texture,
    video: element,
    stream,
    label: track?.label || "camera",
    aspect: height > 0 ? width / height : 16 / 9,
    stop() {
      for (const t of stream.getTracks()) t.stop();
      texture.dispose();
      element.srcObject = null;
    },
  };
}

/**
 * Cover-fit the feed to the viewport without distorting it.
 *
 * three.js stretches a background texture to the canvas by default, which
 * changes apparent object proportions in the video -- unacceptable when a human
 * is judging whether a virtual robot fits a real table.
 */
export function fitBackground(
  texture: THREE.Texture,
  feedAspect: number,
  viewportAspect: number,
): void {
  texture.matrixAutoUpdate = false;
  texture.center.set(0.5, 0.5);

  if (viewportAspect > feedAspect) {
    // Viewport wider than the feed: crop top and bottom.
    const scale = feedAspect / viewportAspect;
    texture.repeat.set(1, scale);
  } else {
    const scale = viewportAspect / feedAspect;
    texture.repeat.set(scale, 1);
  }
  texture.offset.set((1 - texture.repeat.x) / 2, (1 - texture.repeat.y) / 2);
  texture.updateMatrix();
}
