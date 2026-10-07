"""Online estimate of room temperature and heatsink health.

The x17 R2 has no room-temperature sensor, but its heatsink obeys (at steady
state, fans in their loaded regime) a nearly linear law:

    T_sensor ≈ T_ambient + a·P_cpu + b·P_gpu

We fit that law for the CPU and the GPU sensor with recursive least squares
(RLS) over quasi-steady samples. The intercept is the *effective ambient*:
the room temperature as the cooling system experiences it (it reads a little
high if the intake is blocked or the laptop sits on a bed - which is exactly
what the controller needs to know). The slopes `a`/`b` are thermal resistances
in °C/W; if they creep up over weeks, the fins are clogging (dust + humidity)
or the thermal paste is drying out.

Pure Python (no numpy) so it runs anywhere the daemon runs.
"""

from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .model import Sample

Vec = list[float]
Mat = list[list[float]]

AMBIENT_MIN_C = 15.0
AMBIENT_MAX_C = 48.0


@dataclass
class AmbientEstimate:
    ambient_c: float
    std_c: float  # 1-sigma uncertainty of the estimate
    r_cpu: float  # °C per CPU watt on the CPU sensor
    r_gpu: float  # °C per GPU watt on the GPU sensor
    samples: int
    source: str  # "model", "prior", "manual" or "file"


class _RLS:
    """3-parameter RLS with a Gaussian prior, forgetting and anti-windup."""

    def __init__(self, theta0: Vec, p0_diag: Vec, lam: float = 0.998, noise_var: float = 1.0):
        self.theta = list(theta0)
        self.P: Mat = [[p0_diag[i] if i == j else 0.0 for j in range(3)] for i in range(3)]
        self._p0_trace = sum(p0_diag)
        self.lam = lam
        self.r = noise_var
        self.n = 0

    def update(self, x: Vec, y: float) -> None:
        P, th = self.P, self.theta
        Px = [sum(P[i][j] * x[j] for j in range(3)) for i in range(3)]
        denom = self.lam * self.r + sum(x[i] * Px[i] for i in range(3))
        k = [v / denom for v in Px]
        err = y - sum(th[i] * x[i] for i in range(3))
        self.theta = [th[i] + k[i] * err for i in range(3)]
        newP = [[(P[i][j] - k[i] * Px[j]) / self.lam for j in range(3)] for i in range(3)]
        # Anti-windup: without excitation, forgetting inflates P without bound.
        tr = sum(newP[i][i] for i in range(3))
        if tr > self._p0_trace:
            s = self._p0_trace / tr
            newP = [[v * s for v in row] for row in newP]
        self.P = newP
        self.n += 1

    @property
    def intercept_var(self) -> float:
        return max(self.P[0][0], 1e-6)


class _Ema:
    def __init__(self, tau_s: float):
        self.tau = tau_s
        self.v: float | None = None
        self.t: float | None = None

    def update(self, t: float, x: float) -> float:
        if self.v is None or self.t is None:
            self.v, self.t = x, t
            return x
        dt = max(t - self.t, 0.0)
        a = 1.0 - math.exp(-dt / self.tau) if self.tau > 0 else 1.0
        self.v += a * (x - self.v)
        self.t = t
        return self.v


class AmbientEstimator:
    """Feed it every Sample; ask `estimate()` whenever you need the number."""

    def __init__(
        self,
        default_ambient_c: float = 32.0,
        smooth_s: float = 30.0,
        steady_window_s: float = 60.0,
        steady_temp_c: float = 1.0,
        steady_power_w: float = 5.0,
        min_total_power_w: float = 0.0,
        update_every_s: float = 5.0,
        manual_c: float | None = None,
        ambient_file: Path | None = None,
    ):
        self.default = default_ambient_c
        self.manual = manual_c
        self.ambient_file = ambient_file
        prior_var = [25.0, 0.25, 0.05]  # σ 5 °C on ambient, loose on slopes
        self.cpu = _RLS([default_ambient_c, 0.9, 0.2], prior_var)
        self.gpu = _RLS([default_ambient_c, 0.2, 0.35], prior_var)
        self._ema = {k: _Ema(smooth_s) for k in ("tc", "tg", "pc", "pg")}
        self._hist: deque[tuple[float, float, float, float, float]] = deque()
        self._min_sensor_c = math.inf  # ambient can never exceed the coolest sensor
        self.steady_window_s = steady_window_s
        self.steady_temp_c = steady_temp_c
        self.steady_power_w = steady_power_w
        self.min_total_power_w = min_total_power_w
        self.update_every_s = update_every_s
        self._last_update = -math.inf

    def _steady(self, t: float, cur: tuple[float, float, float, float]) -> bool:
        while self._hist and t - self._hist[0][0] > self.steady_window_s:
            self._hist.popleft()
        if not self._hist or t - self._hist[0][0] < self.steady_window_s * 0.8:
            return False
        _, tc0, tg0, pc0, pg0 = self._hist[0]
        tc, tg, pc, pg = cur
        return (
            abs(tc - tc0) <= self.steady_temp_c
            and abs(tg - tg0) <= self.steady_temp_c
            and abs(pc - pc0) <= self.steady_power_w
            and abs(pg - pg0) <= self.steady_power_w
        )

    def update(self, s: Sample) -> None:
        if None in (s.cpu_temp, s.gpu_temp, s.cpu_power, s.gpu_power):
            return
        tc = self._ema["tc"].update(s.t, s.cpu_temp)  # type: ignore[arg-type]
        tg = self._ema["tg"].update(s.t, s.gpu_temp)  # type: ignore[arg-type]
        pc = self._ema["pc"].update(s.t, s.cpu_power)  # type: ignore[arg-type]
        pg = self._ema["pg"].update(s.t, s.gpu_power)  # type: ignore[arg-type]
        cur = (tc, tg, pc, pg)
        self._min_sensor_c = min(self._min_sensor_c * 0.9999 + min(tc, tg) * 0.0001, min(tc, tg)) \
            if math.isfinite(self._min_sensor_c) else min(tc, tg)
        steady = self._steady(s.t, cur)
        self._hist.append((s.t, *cur))
        if not steady or s.t - self._last_update < self.update_every_s:
            return
        if pc + pg < self.min_total_power_w:
            return
        self._last_update = s.t
        x = [1.0, pc, pg]
        self.cpu.update(x, tc)
        self.gpu.update(x, tg)

    def _from_file(self) -> float | None:
        if self.ambient_file is None:
            return None
        try:
            v = float(Path(self.ambient_file).read_text().strip())
        except (OSError, ValueError):
            return None
        return v if AMBIENT_MIN_C - 10 <= v <= AMBIENT_MAX_C + 10 else None

    def estimate(self) -> AmbientEstimate:
        r_cpu, r_gpu = self.cpu.theta[1], self.gpu.theta[2]
        n = min(self.cpu.n, self.gpu.n)
        if self.manual is not None:
            return AmbientEstimate(self.manual, 0.0, r_cpu, r_gpu, n, "manual")
        f = self._from_file()
        if f is not None:
            return AmbientEstimate(f, 0.5, r_cpu, r_gpu, n, "file")
        # Inverse-variance blend of the two sensors' intercepts.
        vc, vg = self.cpu.intercept_var, self.gpu.intercept_var
        amb = (self.cpu.theta[0] / vc + self.gpu.theta[0] / vg) / (1 / vc + 1 / vg)
        std = math.sqrt(1.0 / (1 / vc + 1 / vg))
        # Heat flows out of the laptop, so the room is cooler than the coolest sensor.
        amb = min(amb, self._min_sensor_c - 3.0)
        amb = min(max(amb, AMBIENT_MIN_C), AMBIENT_MAX_C)
        return AmbientEstimate(amb, std, r_cpu, r_gpu, n, "model" if n else "prior")


# --------------------------------------------------------------------------
# Heatsink health ("clean the fins") monitor
# --------------------------------------------------------------------------


@dataclass
class FoulingReport:
    alert: bool
    cpu_rise_pct: float
    gpu_rise_pct: float
    days_of_data: int
    message: str


class FoulingMonitor:
    """Keeps one thermal-resistance snapshot per day in a small JSON file.

    Baseline = mean of the first `baseline_days` snapshots (take them right
    after a cleaning/repaste). Alert when the mean of the most recent
    `recent_days` is `threshold` higher.
    """

    def __init__(self, path: Path, baseline_days: int = 5, recent_days: int = 5, threshold: float = 0.10):
        self.path = Path(path)
        self.baseline_days = baseline_days
        self.recent_days = recent_days
        self.threshold = threshold

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []

    def record(self, est: AmbientEstimate, now: float | None = None, max_std_c: float = 2.0) -> None:
        """Store today's slopes once the model has converged."""
        if est.source != "model" or est.std_c > max_std_c or est.samples < 30:
            return
        day = time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))
        hist = [h for h in self._load() if h.get("day") != day]
        hist.append({"day": day, "r_cpu": round(est.r_cpu, 4), "r_gpu": round(est.r_gpu, 4)})
        hist.sort(key=lambda h: h["day"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(hist[-400:], indent=1))

    def reset(self) -> None:
        """Call after cleaning the heatsink / repasting to start a new baseline."""
        self.path.unlink(missing_ok=True)

    def report(self) -> FoulingReport:
        return self.evaluate(self._load())

    def evaluate(self, hist: list[dict]) -> FoulingReport:
        need = self.baseline_days + self.recent_days
        if len(hist) < need:
            return FoulingReport(False, 0.0, 0.0, len(hist), f"collecting baseline ({len(hist)}/{need} days)")
        base, recent = hist[: self.baseline_days], hist[-self.recent_days :]

        def rise(key: str) -> float:
            b = sum(h[key] for h in base) / len(base)
            r = sum(h[key] for h in recent) / len(recent)
            return (r - b) / b if b > 0 else 0.0

        cr, gr = rise("r_cpu"), rise("r_gpu")
        alert = max(cr, gr) >= self.threshold
        msg = (
            f"Thermal resistance up {max(cr, gr):.0%} since baseline - clean the fan intakes/fins "
            "(see docs/08-hot-humid-climate.md)"
            if alert
            else "heatsink OK"
        )
        return FoulingReport(alert, cr * 100, gr * 100, len(hist), msg)
