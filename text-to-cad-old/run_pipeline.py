"""Entry point: run the multi-agent design pipeline."""
import argparse, json, sys
import ai
from graph import run

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="Mobile rover with a 3-axis arm that "
                                      "passes every simulation criterion")
    ap.add_argument("--no-llm", action="store_true",
                    help="force the deterministic heuristic agents")
    ap.add_argument("--providers", action="store_true", help="show config and exit")
    a = ap.parse_args()

    if a.providers:
        print(ai.describe()); sys.exit(0)

    print(ai.describe(), "\n")
    final = run(task=a.task, use_llm=False if a.no_llm else None)

    print("\n=== rounds ===")
    for h in final.get("history", []):
        mark = "keep  " if h["accepted"] else "reject"
        print(f"  r{h['round']} {mark} {', '.join(h['applied']) or '-':52} "
              f"score {h['score_before']:.3f} -> {h['score_after']:.3f}  "
              f"payload {h['payload_after_g']:.0f} g")
        for r in h["rejected_by_harness"]:
            print(f"         harness rejected: {r}")

    rep = final["report"]
    print(f"\n=== final: {'PASS' if rep['passed'] else 'FAIL'} ===")
    for c in rep["checks"]:
        print(f"  {'PASS' if c['ok'] else 'FAIL'}  {c['name']:14} {c['note']}")
    print("\ndesign:", json.dumps(final["design"], indent=2, default=str))
