"""`cosmos-vss` command line entry point: `doctor` and `analyze`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyzer import AnalyzerError, build_analyzer, source_from_file
from .artifacts import Provenance, sha256_file, write_artifact
from .config import Config
from .prompts import PROMPT_VERSION


def _cmd_doctor(args: argparse.Namespace) -> int:
    ok = True

    try:
        config = Config.from_env()
    except ValueError as exc:
        print(f"FAIL config: {exc}")
        return 1
    print(f"OK   config loaded (backend={config.backend})")
    print(f"OK   backend url resolves ({config.active_base_url})")

    ready = False
    models: list[str] = []
    if config.backend == "mock":
        ready = True
    else:
        try:
            if config.backend == "vss":
                from .vss_client import VssClient

                client = VssClient(config.vss_base_url, timeout_s=10.0)
            else:
                from .cosmos_client import CosmosNimClient

                client = CosmosNimClient(config.cosmos_base_url, timeout_s=10.0)
            ready = client.health_ready()
            if ready:
                models = client.list_models()
        except Exception as exc:
            print(f"FAIL /health/ready check raised: {exc}")
            ready = False

    if ready:
        print("OK   /health/ready")
    else:
        print("FAIL /health/ready — configured backend is not ready")
        ok = False

    if config.backend == "mock":
        print(f"OK   model configured (backend=mock, model={config.active_model})")
    elif not ready:
        print("SKIP model check (backend not ready)")
    elif config.active_model in models:
        print(f"OK   model '{config.active_model}' present in /models")
    else:
        print(f"FAIL model '{config.active_model}' not found in /models: {models}")
        ok = False

    try:
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        probe = config.artifact_dir / ".doctor_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        print(f"OK   artifact directory writable ({config.artifact_dir})")
    except OSError as exc:
        print(f"FAIL artifact directory not writable: {exc}")
        ok = False

    return 0 if ok else 1


def _cmd_analyze(args: argparse.Namespace) -> int:
    config = Config.from_env()
    analyzer = build_analyzer(config)
    video_path = Path(args.video)
    source = source_from_file(video_path)

    try:
        result = analyzer.analyze_full(source, episode_id=args.episode_id)
    except AnalyzerError as exc:
        payload = exc.to_dict()
        if args.json:
            print(json.dumps(payload))
        else:
            print(f"ERROR: {exc.reason}")
        return 1

    source_hash = sha256_file(video_path)
    provenance = Provenance(
        backend=result.episode.backend,
        model=result.episode.model,
        source_sha256=source_hash,
        source_name=result.episode.source_name,
        episode_id=result.episode.episode_id,
        prompt_version=PROMPT_VERSION,
        schema_version=result.episode.schema_version,
    )
    artifact_path = write_artifact(
        config.artifact_dir,
        result.episode,
        raw_response=result.raw_text,
        request_payload={**result.request_payload, "source_sha256": source_hash},
        provenance=provenance,
    )

    if args.json:
        print(result.episode.model_dump_json(indent=2))
    else:
        print(f"semantic_id: {result.episode.semantic_id}")
        print(f"task: {result.episode.task_type}")
        print(f"instruction: {result.episode.instruction}")
        print(f"artifact: {artifact_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cosmos-vss")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check configuration and backend health")
    doctor.set_defaults(func=_cmd_doctor)

    analyze = sub.add_parser("analyze", help="analyze a recorded video")
    analyze.add_argument("video")
    analyze.add_argument("--episode-id", dest="episode_id", default=None)
    analyze.add_argument("--json", dest="json", action="store_true")
    analyze.set_defaults(func=_cmd_analyze)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
