#!/usr/bin/env python3
"""Run the headless smoke test and assert key end-states.

This is what CI should call after every engine change. It plays the
Qingyi route through the headless driver, then verifies:

- the prologue + intro scenes played,
- the climax + ending scenes played,
- key flags are set,
- final affection toward Qingyi is in the 'in love' range.

Exit code 0 = pass, non-zero = something regressed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    script = ROOT / "scripts" / "smoke_test.json"
    cmd = [sys.executable, str(ROOT / "main.py"),
           "--headless", "--script", str(script)]
    print(f"[smoke] running: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        return r.returncode

    # The final command was {"op": "inspect"}, so its result has snapshot.
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        print(f"[smoke] JSON parse failure: {e}\n--- raw ---\n{r.stdout[:500]}")
        return 1

    final = data["final_state"]
    played = set(final["scenes_played"])
    flags = final["flags"]
    qingyi = next((c for c in final["all_characters"] if c["id"] == "qingyi"),
                  None)
    affection = qingyi["affection"] if qingyi else 0

    errs: list[str] = []
    for must_be_played in [
        "prologue_arrival", "prologue_orientation",
        "meet_qingyi", "meet_qingyi_warm_close",
        "qingyi_route_stacks", "qingyi_route_stacks_protected",
        "qingyi_climax", "qingyi_climax_resolved",
        "qingyi_ending",
    ]:
        if must_be_played not in played:
            errs.append(f"scene not played: {must_be_played}")
    for must_be_set in [
        "intro_done", "orientation_done", "met_qingyi",
        "qingyi_stacks_done", "qingyi_truth_resolved",
        "qingyi_arc_done", "ending_qingyi",
    ]:
        if not flags.get(must_be_set):
            errs.append(f"flag not set: {must_be_set}")
    if affection < 80:
        errs.append(f"qingyi affection too low: {affection} (want >= 80)")

    if errs:
        print("[smoke] FAILURES:")
        for e in errs:
            print("  -", e)
        print(json.dumps(final, ensure_ascii=False, indent=2)[:2000])
        return 1
    print(f"[smoke] OK · played {len(played)} scenes · qingyi affection = {affection}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
