"""FastAPI surface for the CAD side of the §6 PCB<->CAD contract.

Endpoint names mirror the MCP tool names one-for-one (`cad.design_enclosure` ->
`POST /cad/design_enclosure`) so the MCP server in `packages/mcp-servers` can be
a thin adapter over this app rather than a second implementation of the logic.

Scope note, stated rather than implied: these calls are **synchronous**. The
master plan's hub contract is "every long op returns a job id; SSE progress"
(§3), and enclosure generation for a board of this size runs in well under a
second, so a job queue would be ceremony. When a tier-2/3 simulation lands
behind `design_enclosure`, that is the moment to move it onto Dramatiq — not
before.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse

from cad_api.contracts import (
    CheckFitRequest,
    ConstrainBoardRequest,
    DesignEnclosureRequest,
    DesignEnclosureResponse,
    Envelope,
    FitResult,
)
from cad_api.enclosure import design_enclosure
from cad_api.fit import check_fit, constrain_board

ARTIFACT_DIR = Path(os.environ.get("CAD_ARTIFACT_DIR", "/tmp/cad-artifacts")).resolve()

app = FastAPI(
    title="STRUCT cad-generation API",
    version="0.1.0",
    description="Feat 2 (text-to-cad) service surface. Implements the §6 PCB<->CAD contract.",
)


@app.get("/health")
def health() -> dict:
    from engine.geometry.registry import generators

    return {
        "ok": True,
        "service": "cad-generation",
        "generators": generators(),
        "artifact_dir": str(ARTIFACT_DIR),
    }


@app.get("/cad/materials")
def materials() -> dict:
    from engine.catalogue import MATERIALS

    return {"materials": MATERIALS.keys()}


@app.post("/cad/design_enclosure", response_model=DesignEnclosureResponse)
def api_design_enclosure(req: DesignEnclosureRequest) -> DesignEnclosureResponse:
    """§6: `cad.design_enclosure(board_report, intent) -> {step, glb, enclosure_report}`."""
    try:
        result = design_enclosure(
            req.board_report,
            req.intent,
            artifact_dir=ARTIFACT_DIR if req.emit_artifacts else None,
        )
    except KeyError as e:  # unknown material / generator — the caller's fault, and nameable
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ValueError as e:  # geometrically impossible request
        raise HTTPException(status_code=422, detail=str(e)) from e

    return DesignEnclosureResponse(
        enclosure_report=result.enclosure_report,
        fit=result.fit,
        ir_json=result.ir.model_dump(mode="json"),
        evaluation=result.evaluation,
    )


@app.post("/cad/check_fit", response_model=FitResult)
def api_check_fit(req: CheckFitRequest) -> FitResult:
    """§6: `cad.check_fit(board_report) -> violations[]`. Deterministic geometry only."""
    return check_fit(req.board_report, req.enclosure_report)


@app.post("/cad/constrain_board", response_model=Envelope)
def api_constrain_board(req: ConstrainBoardRequest) -> Envelope:
    """§6: `cad.constrain_board(reason) -> envelope`."""
    return constrain_board(req.reason, req.enclosure_report, req.intent, req.board_thickness_mm)


class DesignRobotRequest(BaseModel):
    """Prompt -> robot. `intent` is free text; there is deliberately no default."""

    intent: str = Field(min_length=1)
    max_tier: int = 1
    max_iterations: int = 4
    provider: str = "qwen"
    model: str | None = "qwen3.8-27b"
    base_url: str | None = "http://localhost:8100/v1"
    emit_artifacts: bool = True


@app.post("/cad/design_robot")
def api_design_robot(req: DesignRobotRequest) -> dict:
    """Design a whole robot from a text prompt: propose -> evaluate -> revise -> build.

    Synchronous and slow by the standards of the other endpoints — a design loop
    is minutes of model generation, not milliseconds of geometry. This is the
    endpoint the master plan's job-queue rule (§3, "every long op returns a job
    id") is actually about; it runs inline here because the hub's Dramatiq queue
    isn't stood up yet, and a fake job id would be worse than an honest long
    request. Set your client timeout accordingly.
    """
    from cad_api.design import design_robot

    try:
        result = design_robot(
            req.intent,
            max_tier=req.max_tier,
            max_iterations=req.max_iterations,
            provider=req.provider,
            model=req.model,
            base_url=req.base_url,
            artifact_dir=ARTIFACT_DIR if req.emit_artifacts else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:
        if "Connection" in type(e).__name__:
            raise HTTPException(
                status_code=503,
                detail=f"model server at {req.base_url} unreachable: {type(e).__name__}",
            ) from e
        raise

    return {
        "intent": result.intent,
        "converged": result.converged,
        "revisions": len(result.steps),
        "elapsed_s": round(result.elapsed_s, 1),
        "ir": result.ir.model_dump(mode="json") if result.ir is not None else None,
        "evaluation": result.evaluation,
        "assembly": result.assembly,
        "artifacts": result.artifacts,
        "prediction_accuracy": result.prediction_accuracy,
    }


@app.get("/artifacts/{name}")
def artifact(name: str) -> FileResponse:
    """Serve a generated STEP/GLB/STL.

    `name` is resolved and confined to ARTIFACT_DIR so a crafted path cannot
    reach outside it.
    """
    path = (ARTIFACT_DIR / name).resolve()
    if not path.is_relative_to(ARTIFACT_DIR) or not path.is_file():
        raise HTTPException(status_code=404, detail=f"no artifact {name!r}")
    return FileResponse(path)


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("CAD_HOST", "127.0.0.1"),
        port=int(os.environ.get("CAD_PORT", "8200")),
    )


if __name__ == "__main__":
    main()
