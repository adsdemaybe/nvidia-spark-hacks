"""Capture gates."""
from __future__ import annotations

from r2s.core.gates import Severity, check, gate


@gate(id="CAP.FRAME_COUNT", stage="capture", severity=Severity.HARD,
      describe="Enough sharp frames survived filtering for SfM to work.",
      remediation="Record a longer, slower orbit; 40+ frames after filtering.")
def frame_count(art, ctx):
    n = art.payload.frames.count
    return check("CAP.FRAME_COUNT", n >= 40 or n >= 12 and art.header.backend == "stub",
                 measured={"frames": n}, thresholds={"min": 40},
                 msg_fail=f"only {n} frames after filtering")


@gate(id="CAP.SHARPNESS", stage="capture", severity=Severity.HARD,
      describe="10th-percentile Laplacian variance clears the blur floor.",
      remediation="Slow the orbit; avoid low light (raises shutter time).")
def sharpness(art, ctx):
    p10 = art.payload.frames.sharpness_p10
    return check("CAP.SHARPNESS", p10 >= 40.0,
                 measured={"sharpness_p10": round(p10, 1)}, thresholds={"min": 40.0},
                 msg_fail="frames are motion-blurred; SfM will fragment")


@gate(id="CAP.RESOLUTION", stage="capture", severity=Severity.HARD,
      describe="Frames are large enough for feature extraction.",
      remediation="Re-extract with a larger max_width.")
def resolution(art, ctx):
    f = art.payload.frames
    m = min(f.width, f.height)
    return check("CAP.RESOLUTION", m >= 320,
                 measured={"min_dim": m}, thresholds={"min": 320})


@gate(id="CAP.EXPOSURE_STABLE", stage="capture", severity=Severity.SOFT,
      describe="Auto-exposure did not pump during the orbit.",
      remediation="Lock AE/AF before recording (press-and-hold on iPhone).")
def exposure(art, ctx):
    cv = art.payload.frames.exposure_cv
    return check("CAP.EXPOSURE_STABLE", cv <= 0.25,
                 measured={"exposure_cv": round(cv, 3)}, thresholds={"max": 0.25})


@gate(id="CAP.LIDAR_PRESENT", stage="capture", severity=Severity.SOFT,
      describe="A metric LiDAR mesh accompanies the video (the scale anchor).",
      remediation="Scan the room with Polycam/Scaniverse and pass --lidar.")
def lidar_present(art, ctx):
    has = art.payload.lidar is not None
    return check("CAP.LIDAR_PRESENT", has,
                 measured={"lidar": has}, thresholds={"required": "recommended"},
                 msg_fail="no metric anchor; downstream masses/reach are fiction until rescaled")


@gate(id="CAP.LIDAR_SANE", stage="capture", severity=Severity.HARD,
      describe="If present, the LiDAR mesh is room-sized, not garbage.",
      remediation="Re-export the scan; check units (metres, not cm).")
def lidar_sane(art, ctx):
    from r2s.core.gates import GateResult, Verdict, get_gate

    li = art.payload.lidar
    if li is None:
        return GateResult(gate_id="CAP.LIDAR_SANE", verdict=Verdict.SKIP,
                          severity=get_gate("CAP.LIDAR_SANE").severity,
                          message="no LiDAR supplied")
    okay = all(0.5 <= d <= 30.0 for d in li.aabb_m)
    return check("CAP.LIDAR_SANE", okay,
                 measured={"aabb_m": str(tuple(round(d, 2) for d in li.aabb_m))},
                 thresholds={"per_axis": "[0.5, 30] m"})
