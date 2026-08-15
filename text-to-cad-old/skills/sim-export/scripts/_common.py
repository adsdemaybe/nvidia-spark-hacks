"""Shared launcher plumbing for rover-design scripts."""
from __future__ import annotations
import json, os, sys, tempfile
from pathlib import Path

def bootstrap():
    """Put the engine package on sys.path regardless of cwd."""
    root = Path(__file__).resolve().parents[3]
    pkg = root / "packages"
    for p in (str(pkg), str(pkg / "roverkit")):
        if p not in sys.path:
            sys.path.insert(0, p)
    return root

WORKDIR = os.environ.get("ROVER_WORKDIR") or tempfile.mkdtemp(prefix="rover_")

def report_dict(rep) -> dict:
    # Coerce numpy scalars to native types: numpy.bool_ is not JSON
    # serializable, so --format json fails while --format text succeeds.
    return {
        "passed": bool(rep.passed),
        "score": round(float(rep.score), 4),
        "payload_kg": round(float(rep.payload), 4),
        "failing": list(rep.failing()),
        "checks": [
            {"name": str(c.name), "ok": bool(c.ok),
             "value": round(float(c.value), 4),
             "target": round(float(c.target), 4), "note": str(c.note)}
            for c in rep.checks
        ],
    }

def print_report(d: dict, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(d, indent=2)); return
    for c in d["checks"]:
        print(f"  {'PASS' if c['ok'] else 'FAIL'}  {c['name']:14} {c['note']}")
    verdict = "PASS" if d["passed"] else f"FAIL ({', '.join(d['failing'])})"
    print(f"  {'-'*54}\n  score {d['score']:.4f}  payload {d['payload_kg']*1000:.0f} g  -> {verdict}")
