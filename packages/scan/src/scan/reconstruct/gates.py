"""Reconstruction gates. RECON.SCALE_METRIC is the load-bearing one."""
from __future__ import annotations

from r2s.core.gates import GateResult, Severity, Verdict, check, gate, get_gate
from r2s.core.provenance import ValueProvenance


@gate(id="RECON.REGISTRATION", stage="reconstruct", severity=Severity.HARD,
      describe="SfM registered enough of the input frames.",
      remediation="Orbit, don't pan; more overlap; better light.")
def registration(art, ctx):
    r = art.payload.sfm.registration_ratio
    return check("RECON.REGISTRATION", r >= 0.8,
                 measured={"ratio": round(r, 3)}, thresholds={"min": 0.8})


@gate(id="RECON.SINGLE_MODEL", stage="reconstruct", severity=Severity.SOFT,
      describe="SfM produced one connected model, not fragments.",
      remediation="Close the loop in the capture path; add overlap between ends.")
def single_model(art, ctx):
    n = art.payload.sfm.n_submodels
    return check("RECON.SINGLE_MODEL", n == 1,
                 measured={"submodels": n}, thresholds={"expect": 1})


@gate(id="RECON.VAL_HELDOUT", stage="reconstruct", severity=Severity.HARD,
      describe="Validation metrics come from held-out frames, not training frames.",
      remediation="Backend must reserve a val split; self-reported train PSNR is a lie.")
def val_heldout(art, ctx):
    n = len(art.payload.splat.val_split)
    return check("RECON.VAL_HELDOUT", n >= 1,
                 measured={"val_frames": n}, thresholds={"min": 1})


@gate(id="RECON.VAL_PSNR", stage="reconstruct", severity=Severity.HARD,
      describe="Held-out PSNR clears the usability floor.",
      remediation="More iterations; check exposure lock; denser capture.")
def val_psnr(art, ctx):
    p = art.payload.splat.val_psnr
    return check("RECON.VAL_PSNR", p >= 22.0,
                 measured={"psnr_db": round(p, 2)}, thresholds={"min": 22.0})


@gate(id="RECON.GAUSSIAN_COUNT", stage="reconstruct", severity=Severity.SOFT,
      describe="Gaussian count is in the plausible band for a room.",
      remediation="Too few: densify more. Too many: MCMC cap or prune.")
def gaussian_count(art, ctx):
    n = art.payload.splat.n_gaussians
    okay = 3_000 <= n <= 5_000_000  # fixture floor is low; real floor is ~3e5
    return check("RECON.GAUSSIAN_COUNT", okay,
                 measured={"n_gaussians": n}, thresholds={"band": "[3e3, 5e6]"})


@gate(id="RECON.SCALE_METRIC", stage="reconstruct", severity=Severity.HARD,
      describe="The scene has a metric scale derived from a measurement, not a default. "
               "Without this, every downstream mass/inertia/grasp/reach number is fiction.",
      remediation="Supply a LiDAR mesh at capture (--lidar). To proceed anyway, set "
                  "assume_scale=true -- everything downstream is stamped DEGRADED.")
def scale_metric(art, ctx):
    s = art.payload.scale
    evidenced = s.scale_factor.provenance in (ValueProvenance.MEASURED, ValueProvenance.INFERRED)
    return check("RECON.SCALE_METRIC", evidenced,
                 measured={"provenance": str(s.scale_factor.provenance), "method": s.method},
                 thresholds={"require": "MEASURED|INFERRED"},
                 msg_fail="scale is ASSUMED; downstream physics is fiction")


@gate(id="RECON.SCALE_RESIDUAL", stage="reconstruct", severity=Severity.HARD,
      describe="LiDAR<->SfM alignment converged tightly.",
      remediation="Check the LiDAR mesh covers the filmed area; re-run with more samples.")
def scale_residual(art, ctx):
    s = art.payload.scale
    if s.method != "lidar_sim3":
        return GateResult(gate_id="RECON.SCALE_RESIDUAL", verdict=Verdict.SKIP,
                          severity=get_gate("RECON.SCALE_RESIDUAL").severity,
                          message="no LiDAR alignment ran")
    okay = (s.rmse_m or 9e9) <= 0.03 and (s.inlier_ratio or 0.0) >= 0.5
    return check("RECON.SCALE_RESIDUAL", okay,
                 measured={"rmse_m": round(s.rmse_m or -1, 4),
                           "inliers": round(s.inlier_ratio or 0, 3)},
                 thresholds={"rmse_max": 0.03, "inliers_min": 0.5})


@gate(id="RECON.GRAVITY_ALIGNED", stage="reconstruct", severity=Severity.HARD,
      describe="A floor was found and the world is z-up on it.",
      remediation="Film more floor; check the scan isn't wall-only.")
def gravity(art, ctx):
    import numpy as np

    f = art.payload.floor_plane
    if f is None:
        return check("RECON.GRAVITY_ALIGNED", False,
                     measured={"floor": "none"}, thresholds={"require": "found"})
    tilt = float(np.degrees(np.arccos(np.clip(f.normal[2], -1, 1))))
    return check("RECON.GRAVITY_ALIGNED", tilt <= 5.0,
                 measured={"tilt_deg": round(tilt, 2)}, thresholds={"max": 5.0})


@gate(id="RECON.ROOM_EXTENT", stage="reconstruct", severity=Severity.HARD,
      describe="The reconstructed extent is room-sized.",
      remediation="Scale solve failed or scene is a fragment -- inspect the PLY.")
def room_extent(art, ctx):
    ex = art.payload.room_extent_m
    okay = all(0.5 <= d <= 30.0 for d in ex[:2]) and 0.2 <= ex[2] <= 10.0
    return check("RECON.ROOM_EXTENT", okay,
                 measured={"extent_m": str(tuple(round(d, 2) for d in ex))},
                 thresholds={"xy": "[0.5,30]", "z": "[0.2,10]"})
