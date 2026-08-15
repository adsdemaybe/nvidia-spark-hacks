"""r2s -- umbrella CLI. `r2s run-all` is the whole pipeline and the sim test.

The fixture invocation IS the validation loop:

    r2s run-all fixtures/tiny_room --fixture -n 8 --seed 1337

runs capture -> reconstruct -> segment -> assetize -> generate -> shell ->
cousins -> tasks -> validate, gate tables per stage, exit = max gate code.
CI and the coding loop both live against that command.
"""
from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console

from r2s.core.gates import EXIT_MEANING, ExitCode, all_gates, render
from r2s.core.runctx import RunContext, run_stage
from r2s.core.store import CASStore

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


def _ctx(run_id: str, seed: int, store_root: str | None, force: str = "") -> RunContext:
    root = store_root or f"file://{Path.cwd() / '.r2s'}"
    return RunContext(
        run_id=run_id,
        store=CASStore(root),
        seed=seed,
        work_dir=Path.cwd() / ".r2s" / "work",
        force_stages=set(f for f in force.split(",") if f),
    )


@app.command("run-all")
def run_all(
    source: str = typer.Argument(..., help="video file, frame dir, or fixture dir"),
    fixture: bool = typer.Option(False, "--fixture", help="source is a fixture dir "
                                 "(frames/ + gaussians.ply + clusters.json + lidar.glb)"),
    n: int = typer.Option(8, "-n", help="number of cousin scenes"),
    seed: int = typer.Option(1337, "--seed"),
    run_id: str = typer.Option("", "--run", help="default: derived from source+seed"),
    lidar: str | None = typer.Option(None, "--lidar"),
    force: str = typer.Option("", "--force", help="comma-list of stages to re-run"),
    store: str | None = typer.Option(None, "--store"),
    strict: bool = typer.Option(False, "--strict", help="soft failures block too"),
):
    from scan.assetize import stage as assetize_s
    from scan.capture import gates as _cg  # noqa: F401  (gate registration)
    from scan.capture import stage as capture_s
    from scan.assetize import gates as _ag  # noqa: F401
    from scan.generate import gates as _gg  # noqa: F401
    from scan.generate import stage as generate_s
    from scan.reconstruct import gates as _rg  # noqa: F401
    from scan.reconstruct import stage as reconstruct_s
    from scan.segment import gates as _sg  # noqa: F401
    from scan.segment import stage as segment_s
    from envgen.cousins import gates as _cog  # noqa: F401
    from envgen.cousins import stage as cousins_s
    from envgen.shell import gates as _shg  # noqa: F401
    from envgen.shell import stage as shell_s
    from envgen.tasks import gates as _tg  # noqa: F401
    from envgen.tasks import stage as tasks_s
    from envgen.validate import stage as validate_s

    src = Path(source)
    if not src.exists():
        console.print(f"[red]source not found: {src}[/red]")
        raise typer.Exit(int(ExitCode.MISSING_INPUT))
    rid = run_id or f"{src.stem}-s{seed}"
    ctx = _ctx(rid, seed, store, force)
    ctx.extra["n_scenes"] = n
    fixture_dir = str(src) if fixture else None

    if fixture:
        video_arg = str(src / "frames")
        lidar_arg = lidar or (str(src / "lidar.glb") if (src / "lidar.glb").exists() else None)
    else:
        video_arg, lidar_arg = str(src), lidar

    worst = ExitCode.PASS
    outcomes = {}

    def do(spec, fn, inputs, cfg):
        nonlocal worst
        console.rule(f"[bold]{spec.name}[/bold]")
        out = run_stage(spec, fn, inputs, cfg, ctx)
        render(out.gate_report, console)
        code = out.exit_code
        if strict and code == int(ExitCode.PASS_DEGRADED):
            code = int(ExitCode.GATE_FAIL)
        worst = ExitCode(max(int(worst), code))
        if out.cached:
            console.print("[dim](cached)[/dim]")
        outcomes[spec.name] = out
        if out.gate_report.blocked:
            console.print(f"[red]hard gate failed in {spec.name}; stopping.[/red]")
            _summary(outcomes, worst)
            raise typer.Exit(int(worst))
        return _load(ctx, out)

    cap = do(capture_s.SPEC, capture_s.run, {},
             capture_s.CaptureConfig(video=video_arg, lidar=lidar_arg))
    rec = do(reconstruct_s.SPEC, reconstruct_s.run, {"capture": cap},
             reconstruct_s.ReconstructConfig(
                 backend="stub" if fixture else "three_dgrut", fixture_dir=fixture_dir))
    seg = do(segment_s.SPEC, segment_s.run, {"reconstruct": rec},
             segment_s.SegmentConfig(
                 backend="fixture" if fixture else "hf", fixture_dir=fixture_dir))
    bundle = do(assetize_s.SPEC, assetize_s.run,
                {"segment": seg, "reconstruct": rec}, assetize_s.AssetizeConfig())
    gen = do(generate_s.SPEC, generate_s.run, {"segment": seg},
             generate_s.GenerateConfig(
                 source="procedural" if fixture else "trellis",
                 fixture_root=str(Path("fixtures/assets"))))
    shl = do(shell_s.SPEC, shell_s.run, {"reconstruct": rec, "segment": seg},
             shell_s.ShellConfig())
    cous = do(cousins_s.SPEC, cousins_s.run,
              {"shell": shl, "assetize": bundle, "generate": gen, "segment": seg},
              cousins_s.CousinsConfig(n_scenes=n))
    do(tasks_s.SPEC, tasks_s.run,
       {"cousins": cous, "assetize": bundle, "generate": gen}, tasks_s.TasksConfig())
    do(validate_s.SPEC, validate_s.run, {"cousins": cous}, validate_s.ValidateConfig())

    _summary(outcomes, worst)
    raise typer.Exit(int(worst))


def _load(ctx, outcome):
    from r2s.core.artifacts import BaseArtifact
    from r2s.core.schemas import ARTIFACT_TYPES

    from envgen.cousins.stage import CousinSetPayload
    from envgen.tasks.stage import TaskSetPayload
    from envgen.validate.stage import ValidationPayload

    extra = {"cousin_set": CousinSetPayload, "task_set": TaskSetPayload,
             "validation": ValidationPayload}
    art_type = outcome.gate_report.stage
    ref = outcome.artifact_ref
    raw = ctx.store.get_bytes(ref)
    import json

    t = json.loads(raw)["header"]["artifact_type"]
    model = ARTIFACT_TYPES.get(t) or extra.get(t)
    return ctx.store.get_artifact(ref, BaseArtifact[model])


def _summary(outcomes, worst):
    console.rule("[bold]summary[/bold]")
    for name, o in outcomes.items():
        style = {"PASS": "green", "PASS_DEGRADED": "yellow", "FAIL": "red"}[o.gate_report.verdict]
        console.print(
            f"  [{style}]{o.gate_report.verdict:14}[/{style}] {name:12} "
            f"{'(cached)' if o.cached else f'{o.wallclock_s:6.1f}s'}"
        )
    console.print(f"\nexit {int(worst)}: {EXIT_MEANING[worst]}")


@app.command()
def doctor():
    """Local dependency inventory (host preflight is spark/preflight.sh)."""
    import importlib

    mods = ["numpy", "scipy", "trimesh", "shapely", "mujoco", "cv2", "plyfile",
            "coacd", "pxr", "PIL", "fsspec", "pydantic", "pycolmap", "torch"]
    for m in mods:
        try:
            mod = importlib.import_module(m)
            v = getattr(mod, "__version__", "?")
            console.print(f"  [green]ok[/green]      {m} {v}")
        except ImportError:
            console.print(f"  [yellow]absent[/yellow]  {m}")
    raise typer.Exit(0)


@app.command("gates")
def gates_cmd(explain: str = typer.Option("", "--explain", help="gate id")):
    """List registered gates, or explain one."""
    # import for side-effect registration
    for mod in ("scan.capture.gates", "scan.reconstruct.gates", "scan.segment.gates",
                "scan.assetize.gates", "scan.generate.gates", "envgen.shell.gates",
                "envgen.cousins.gates", "envgen.tasks.gates"):
        __import__(mod)
    from r2s.core.gates import get_gate

    if explain:
        g = get_gate(explain)
        if g is None:
            console.print(f"[red]no gate {explain!r}[/red]")
            raise typer.Exit(2)
        console.print(f"[bold]{g.id}[/bold]  stage={g.stage}  severity={g.severity}")
        console.print(f"  {g.describe}")
        console.print(f"  [dim]remedy: {g.remediation}[/dim]")
        raise typer.Exit(0)
    for g in all_gates():
        console.print(f"  {g.stage:12} {g.id:28} {g.severity}")
    raise typer.Exit(0)


@app.command()
def status(run: str = typer.Argument(...), store: str | None = typer.Option(None)):
    """Stage table for a run."""
    ctx = _ctx(run, 0, store)
    m = ctx.manifest()
    for stage, e in m.get("stages", {}).items():
        console.print(f"  {stage:12} {e['verdict']:14} cached={e['cached']} "
                      f"{e['wallclock_s']:8.1f}s  {e['artifact_ref']['sha256'][:12]}")
    raise typer.Exit(0)


if __name__ == "__main__":
    app()
