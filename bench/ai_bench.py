#!/usr/bin/env python3
"""AI throughput and efficiency benchmark for the RTX 3070 Ti Laptop.

    python bench/ai_bench.py matmul --seconds 60 --room 33
    python bench/ai_bench.py llama --model path/to/model.gguf --room 33   (needs llama-bench in PATH)

Reports throughput, average GPU power (NVML), throughput per watt and peak
temperatures, and appends one JSON line per run to bench/results.jsonl so
before/after runs (and runs at different room temperatures) can be compared
with bench/compare.py.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

RESULTS = Path(__file__).with_name("results.jsonl")


class PowerSampler(threading.Thread):
    """Samples GPU power/temp/clock every 0.25 s while a benchmark runs."""

    def __init__(self):
        super().__init__(daemon=True)
        self.samples: list[tuple[float, float, float]] = []
        self._stop = threading.Event()
        try:
            import pynvml  # type: ignore

            pynvml.nvmlInit()
            self.nv, self.h = pynvml, pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception as e:  # noqa: BLE001
            print(f"(NVML unavailable: {e}; power figures will be missing)", file=sys.stderr)
            self.nv = self.h = None

    def run(self):
        while not self._stop.is_set() and self.h is not None:
            nv, h = self.nv, self.h
            self.samples.append(
                (
                    nv.nvmlDeviceGetPowerUsage(h) / 1000.0,
                    float(nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU)),
                    float(nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_GRAPHICS)),
                )
            )
            time.sleep(0.25)

    def stop(self) -> dict:
        self._stop.set()
        self.join(timeout=2)
        if not self.samples:
            return {}
        p = [s[0] for s in self.samples]
        return {
            "gpu_power_avg_w": round(sum(p) / len(p), 1),
            "gpu_temp_max_c": max(s[1] for s in self.samples),
            "gpu_clock_avg_mhz": round(sum(s[2] for s in self.samples) / len(self.samples)),
        }


def matmul(seconds: float, n: int = 8192) -> dict:
    import torch  # type: ignore

    if not torch.cuda.is_available():
        raise SystemExit("CUDA not available to PyTorch")
    a = torch.randn(n, n, device="cuda", dtype=torch.float16)
    b = torch.randn(n, n, device="cuda", dtype=torch.float16)
    for _ in range(3):
        a @ b
    torch.cuda.synchronize()
    iters, t0 = 0, time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        for _ in range(10):
            a @ b
        torch.cuda.synchronize()
        iters += 10
    dt = time.perf_counter() - t0
    return {"metric": "fp16_tflops", "value": round(2 * n**3 * iters / dt / 1e12, 2)}


_LLAMA_TG = re.compile(r"\|\s*tg\d+\s*\|\s*([\d.]+)\s*±")
_LLAMA_PP = re.compile(r"\|\s*pp\d+\s*\|\s*([\d.]+)\s*±")


def parse_llama_bench(text: str) -> dict:
    tg = [float(x) for x in _LLAMA_TG.findall(text)]
    pp = [float(x) for x in _LLAMA_PP.findall(text)]
    out = {}
    if tg:
        out.update(metric="tg_tok_per_s", value=tg[-1])
    if pp:
        out["pp_tok_per_s"] = pp[-1]
    return out


def llama(model: str, reps: int) -> dict:
    exe = shutil.which("llama-bench")
    if exe is None:
        raise SystemExit("llama-bench not found in PATH (build llama.cpp with CUDA)")
    res = subprocess.run(
        [exe, "-m", model, "-ngl", "99", "-p", "512", "-n", "256", "-r", str(reps)],
        capture_output=True,
        text=True,
        check=True,
    )
    print(res.stdout)
    out = parse_llama_bench(res.stdout)
    if not out:
        raise SystemExit("could not parse llama-bench output")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["matmul", "llama"])
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--model")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--room", type=float, required=True, help="room temperature in °C (measure it!)")
    ap.add_argument("--label", default="", help="e.g. 'stock' or 'x17tune'")
    a = ap.parse_args(argv)

    sampler = PowerSampler()
    sampler.start()
    t0 = time.time()
    res = matmul(a.seconds) if a.kind == "matmul" else llama(a.model, a.reps)
    res.update(sampler.stop())
    if res.get("gpu_power_avg_w"):
        res["per_watt"] = round(res["value"] / res["gpu_power_avg_w"], 4)
    res.update(kind=a.kind, label=a.label, room_c=a.room, time=int(t0))
    print(json.dumps(res, indent=2))
    with RESULTS.open("a") as f:
        f.write(json.dumps(res) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
