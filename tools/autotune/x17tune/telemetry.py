"""CSV telemetry: one row per tick, for benchmarking and tuning."""

from __future__ import annotations

import csv
from pathlib import Path

from .model import Decision, Sample

FIELDS = [
    "t",
    "workload",
    "band",
    "ambient_c",
    "cpu_temp",
    "cpu_power",
    "cpu_util",
    "gpu_temp",
    "gpu_power",
    "gpu_util",
    "gpu_clock",
    "fan_rpm",
    "cpu_throttling",
    "gpu_throttling",
    "thermal_mode",
    "cpu_pl1_w",
    "cpu_pl2_w",
    "cpu_epp",
    "gpu_clock_max_mhz",
    "gpu_profile",
    "fan_boost",
    "on_ac",
]


def _r(v):
    return round(v, 2) if isinstance(v, float) else v


def row(s: Sample, d: Decision) -> dict:
    return {
        "t": round(s.t, 1),
        "workload": d.workload.value,
        "band": d.band,
        "ambient_c": d.ambient_c,
        "cpu_temp": _r(s.cpu_temp),
        "cpu_power": _r(s.cpu_power),
        "cpu_util": _r(s.cpu_util),
        "gpu_temp": _r(s.gpu_temp),
        "gpu_power": _r(s.gpu_power),
        "gpu_util": _r(s.gpu_util),
        "gpu_clock": _r(s.gpu_clock),
        "fan_rpm": "/".join(str(x) for x in s.fan_rpm),
        "cpu_throttling": int(s.cpu_throttling),
        "gpu_throttling": int(s.gpu_throttling),
        "thermal_mode": d.thermal_mode.value,
        "cpu_pl1_w": d.cpu_pl1_w,
        "cpu_pl2_w": d.cpu_pl2_w,
        "cpu_epp": d.cpu_epp,
        "gpu_clock_max_mhz": d.gpu_clock_max_mhz if d.gpu_clock_max_mhz is not None else "",
        "gpu_profile": d.gpu_profile if d.gpu_profile is not None else "",
        "fan_boost": d.fan_boost if d.fan_boost is not None else "",
        "on_ac": int(s.on_ac),
    }


class CsvLogger:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        self._f = self.path.open("a", newline="", encoding="utf-8")
        self._w = csv.DictWriter(self._f, fieldnames=FIELDS)
        if new:
            self._w.writeheader()

    def log(self, s: Sample, d: Decision) -> None:
        self._w.writerow(row(s, d))
        self._f.flush()

    def close(self) -> None:
        self._f.close()


def summarize(path: Path | str) -> dict:
    """Aggregate a telemetry CSV: averages, maxima and throttle events per hour."""
    rows = list(csv.DictReader(Path(path).open(encoding="utf-8")))
    if not rows:
        return {}

    def nums(k):
        out = []
        for r in rows:
            try:
                out.append(float(r[k]))
            except (KeyError, ValueError):
                pass
        return out

    hours = max((float(rows[-1]["t"]) - float(rows[0]["t"])) / 3600.0, 1 / 3600)
    res: dict = {"rows": len(rows), "hours": round(hours, 3)}
    for k in ("cpu_temp", "gpu_temp", "cpu_power", "gpu_power", "gpu_clock", "ambient_c"):
        v = nums(k)
        if v:
            res[f"{k}_avg"] = round(sum(v) / len(v), 1)
            res[f"{k}_max"] = round(max(v), 1)
    # A throttle *event* = transition from not-throttling to throttling.
    for k in ("cpu_throttling", "gpu_throttling"):
        v = [int(float(x)) for x in (r.get(k, "0") or "0" for r in rows)]
        events = sum(1 for a, b in zip([0, *v], v) if b and not a)
        res[f"{k}_events_per_h"] = round(events / hours, 1)
    return res
