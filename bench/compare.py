#!/usr/bin/env python3
"""Compare benchmark runs or telemetry summaries side by side.

    python bench/compare.py results            # bench/results.jsonl grouped by label
    python bench/compare.py csv before.csv after.csv   # x17tune telemetry CSVs

Always compare runs made at a similar room temperature (±1.5 °C); the tables
show room temperature so mismatched runs are obvious.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "autotune"))


def group_results(lines: list[dict]) -> dict[tuple, dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in lines:
        groups.setdefault((r.get("kind"), r.get("label", "")), []).append(r)
    out = {}
    for k, rs in groups.items():
        def avg(key):
            v = [r[key] for r in rs if isinstance(r.get(key), (int, float))]
            return round(sum(v) / len(v), 3) if v else None

        out[k] = {
            "runs": len(rs),
            "value": avg("value"),
            "per_watt": avg("per_watt"),
            "gpu_power_avg_w": avg("gpu_power_avg_w"),
            "gpu_temp_max_c": avg("gpu_temp_max_c"),
            "room_c": avg("room_c"),
        }
    return out


def pct(a, b):
    return f"{(b - a) / a * 100:+.1f}%" if a and b is not None else "-"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("results")
    p.add_argument("--file", type=Path, default=Path(__file__).with_name("results.jsonl"))
    p = sub.add_parser("csv")
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    a = ap.parse_args(argv)

    if a.cmd == "results":
        lines = [json.loads(x) for x in a.file.read_text().splitlines() if x.strip()]
        print(f"{'kind':8} {'label':12} {'runs':>4} {'value':>9} {'per W':>8} {'GPU W':>7} {'GPU max':>8} {'room':>6}")
        for (kind, label), g in sorted(group_results(lines).items()):
            print(
                f"{kind:8} {label:12} {g['runs']:>4} {g['value']!s:>9} {g['per_watt']!s:>8} "
                f"{g['gpu_power_avg_w']!s:>7} {g['gpu_temp_max_c']!s:>8} {g['room_c']!s:>6}"
            )
        return 0

    from x17tune.telemetry import summarize

    b, c = summarize(a.before), summarize(a.after)
    print(f"{'metric':32} {'before':>10} {'after':>10} {'change':>9}")
    for k in sorted(set(b) | set(c)):
        print(f"{k:32} {b.get(k, '-')!s:>10} {c.get(k, '-')!s:>10} {pct(b.get(k), c.get(k)):>9}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
