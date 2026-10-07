#!/usr/bin/env python3
"""Find the RTX 3070 Ti Laptop's efficiency knee and check undervolt stability.

sweep   Locks the GPU clock ceiling at several points (nvidia-smi -lgc, admin),
        runs an fp16 matmul load at each, and reports TFLOPS, watts, TFLOPS/W
        and temperature. Recommends the "knee": the highest clock whose
        efficiency is within `--knee-pct` of the best. That is the ceiling to
        put in profiles.yaml (ai_inference.gpu_clock_max_mhz) and the target
        frequency for your Afterburner curve points.

verify  Runs the load with results checked against a CPU-side reference every
        few seconds. Use it after building an Afterburner V/F-curve undervolt:
        any mismatch, NaN or driver reset = not stable, raise the voltage a step.

It never writes a V/F curve itself; that is done in MSI Afterburner (Ctrl+F),
saved to profile slots 1-3, which x17tune then switches between.

    python bench/gpu_curve_search.py sweep --points 1200 1400 1500 1600 1700 1800 1900 --seconds 45
    python bench/gpu_curve_search.py verify --minutes 20
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai_bench import PowerSampler  # noqa: E402


def knee(points: list[dict], pct: float = 5.0) -> dict | None:
    """Highest-clock point whose perf/W is within pct % of the best perf/W."""
    eff = [p for p in points if p.get("per_watt")]
    if not eff:
        return None
    best = max(p["per_watt"] for p in eff)
    ok = [p for p in eff if p["per_watt"] >= best * (1 - pct / 100)]
    return max(ok, key=lambda p: p["clock"])


def _lock(mhz: int | None) -> None:
    argv = ["nvidia-smi", "-rgc"] if mhz is None else ["nvidia-smi", "-lgc", f"210,{mhz}"]
    subprocess.run(argv, check=True, capture_output=True)


def sweep(points: list[int], seconds: float, cooldown: float) -> list[dict]:
    from ai_bench import matmul

    out = []
    try:
        for mhz in points:
            _lock(mhz)
            time.sleep(cooldown)
            s = PowerSampler()
            s.start()
            r = matmul(seconds)
            r.update(s.stop())
            r["clock"] = mhz
            if r.get("gpu_power_avg_w"):
                r["per_watt"] = round(r["value"] / r["gpu_power_avg_w"], 4)
            print(
                f"{mhz:5d} MHz: {r['value']:6.2f} TFLOPS  {r.get('gpu_power_avg_w', '?'):>6} W  "
                f"{r.get('per_watt', '?')} TFLOPS/W  max {r.get('gpu_temp_max_c', '?')} °C",
                flush=True,
            )
            out.append(r)
    finally:
        _lock(None)
    return out


def verify(minutes: float, n: int = 4096) -> bool:
    import torch  # type: ignore

    torch.backends.cuda.matmul.allow_tf32 = False  # full fp32 so results are comparable to the reference
    a = torch.randn(n, n, dtype=torch.float32)
    b = torch.randn(n, n, dtype=torch.float32)
    ref = (a.double() @ b.double()).float()
    ga, gb = a.cuda(), b.cuda()
    end = time.time() + minutes * 60
    checks = 0
    while time.time() < end:
        for _ in range(50):
            c = ga @ gb
        torch.cuda.synchronize()
        err = (c.cpu() - ref).abs().max().item()
        checks += 1
        if not err < 1e-1:  # also catches NaN (comparison is False)
            print(f"UNSTABLE after {checks} checks: max error {err}")
            return False
    print(f"stable: {checks} checks over {minutes:g} min")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sweep")
    p.add_argument("--points", type=int, nargs="+", default=[1200, 1400, 1500, 1600, 1700, 1800, 1900])
    p.add_argument("--seconds", type=float, default=45)
    p.add_argument("--cooldown", type=float, default=10)
    p.add_argument("--knee-pct", type=float, default=5.0)
    p = sub.add_parser("verify")
    p.add_argument("--minutes", type=float, default=20)
    a = ap.parse_args(argv)
    if a.cmd == "sweep":
        pts = sweep(a.points, a.seconds, a.cooldown)
        k = knee(pts, a.knee_pct)
        if k:
            print(f"\nEfficiency knee: {k['clock']} MHz ({k['per_watt']} TFLOPS/W). "
                  "Use it for ai_inference.gpu_clock_max_mhz and as the Afterburner slot-1 target.")
        return 0
    return 0 if verify(a.minutes) else 1


if __name__ == "__main__":
    sys.exit(main())
