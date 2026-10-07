"""The control loop: workload + climate band + temperatures -> Decision.

Two PI loops share one heatsink:

* CPU loop drives sustained package power (PL1) between the profile ceiling
  (scaled by the climate band) and the hard floor. Its error is the smaller
  of the CPU's own headroom and - for GPU-centric workloads - the GPU's
  headroom, so the CPU gives up watts first when the shared heat-pipes are
  saturated. That moves capacity to where frames/tokens are made.
* GPU loop drives the locked max clock between the profile floor and ceiling.
  Lower clocks also mean lower voltage on Ampere's V/F curve, so every
  clock step down saves disproportionate power (P ~ f·V²).

Both integrators only ever pull *down* from the ceiling (anti-windup at 0),
so in a cool room the loops are idle and the profile runs at full strength.
"""

from __future__ import annotations

import math

from .ambient import AmbientEstimate, AmbientEstimator
from .classifier import Classifier
from .climate import BandSelector
from .config import Config, Profile
from .model import Decision, Sample, Workload

EMERGENCY_MARGIN_C = 4.0  # over target -> force max fan boost
EMERGENCY_CLEAR_C = 1.0  # back below target - this -> release
DEADBAND_C = 1.0  # no integration within ±this of target: kills quantization limit cycles


YIELD_MARGIN_C = 2.0  # CPU yields only once the GPU is this far over target (GPU loop acts first)
RAISE_PERIOD_S = 30.0  # raise GPU clocks by at most one step of gpu_min_change per this period
OVERSHOOT_C = 3.0  # beyond this far over target the proportional gain triples


def _db(e: float) -> float:
    return 0.0 if abs(e) <= DEADBAND_C else e


def _p_term(kp: float, e: float) -> float:
    """Proportional cut for over-temperature only (e < 0), steeper past OVERSHOOT_C."""
    e = min(_db(e), 0.0)
    if e < -OVERSHOOT_C:
        return kp * (-OVERSHOOT_C + 3.0 * (e + OVERSHOOT_C))
    return kp * e


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


class _Ema:
    def __init__(self, tau: float):
        self.tau, self.v, self.t = tau, None, None

    def update(self, t: float, x: float | None) -> float | None:
        if x is None:
            return self.v
        if self.v is None or self.t is None:
            self.v, self.t = x, t
        else:
            a = 1.0 - math.exp(-max(t - self.t, 0.0) / self.tau) if self.tau > 0 else 1.0
            self.v += a * (x - self.v)
            self.t = t
        return self.v


class Controller:
    def __init__(
        self,
        cfg: Config,
        classifier: Classifier | None = None,
        estimator: AmbientEstimator | None = None,
        forced_workload: Workload | None = None,
    ):
        self.cfg = cfg
        c = cfg.controller
        self.classifier = classifier or Classifier(cfg.rules, hold_s=c.hold_s)
        self.estimator = estimator or AmbientEstimator(default_ambient_c=cfg.default_ambient_c)
        self.bands = BandSelector(cfg.bands, cfg.hysteresis_c, initial_ambient_c=cfg.default_ambient_c)
        self.forced = forced_workload
        self._tc = _Ema(c.smoothing_s)
        self._tg = _Ema(c.smoothing_s)
        self._i_cpu = 0.0
        self._i_gpu = 0.0
        self._last_t: float | None = None
        self._pl1_out: float | None = None
        self._gpu_out: int | None = None
        self._gpu_raised_at = -1e9
        self._emergency = False
        self.last_estimate: AmbientEstimate | None = None

    # -- helpers -----------------------------------------------------------

    def _cpu_ceiling(self, prof: Profile, scale: float) -> float:
        lo, hi = self.cfg.limits.cpu_pl1_w
        return _clamp(prof.cpu_pl1_w * scale, lo, hi)

    def _gpu_range(self, prof: Profile, scale: float) -> tuple[int, int, bool]:
        """(floor, ceiling, unlocked_when_at_ceiling)."""
        lo, hi = self.cfg.limits.gpu_clock_mhz
        floor = int(_clamp(prof.gpu_clock_floor_mhz, lo, hi))
        if prof.gpu_clock_max_mhz is None:
            return floor, hi, True
        # Power ~ f^2.2 along the V/F curve, so scale clocks by power_scale^(1/2.2).
        ceil = int(_clamp(prof.gpu_clock_max_mhz * scale ** (1 / 2.2), floor, hi))
        return floor, ceil, False

    # -- main step -----------------------------------------------------------

    def step(self, s: Sample) -> Decision:
        cfg, c = self.cfg, self.cfg.controller
        dt = c.tick_s if self._last_t is None else _clamp(s.t - self._last_t, 0.0, 10 * c.tick_s)
        self._last_t = s.t

        workload = self.forced or self.classifier.update(s)
        if not s.on_ac:
            workload = Workload.BATTERY  # never run a plugged-in profile on battery
        self.estimator.update(s)
        est = self.estimator.estimate()
        self.last_estimate = est
        trusted = est.source in ("manual", "file") or est.std_c <= cfg.max_ambient_std_c
        band = self.bands.update(est.ambient_c if trusted else cfg.default_ambient_c)
        prof = cfg.profiles[workload]
        scale = band.power_scale

        cpu_target = cfg.cpu_target_c + band.temp_offset_c
        gpu_target = cfg.gpu_target_c + band.temp_offset_c
        tc = self._tc.update(s.t, s.cpu_temp)
        tg = self._tg.update(s.t, s.gpu_temp)
        notes: list[str] = []

        # ---- CPU PL1 loop ----
        pl1_ceil = self._cpu_ceiling(prof, scale)
        pl1_floor = min(max(prof.cpu_pl1_floor_w, cfg.limits.cpu_pl1_w[0]), pl1_ceil)
        if tc is None:
            pl1 = pl1_ceil
            notes.append("no CPU temp: open-loop PL1")
        else:
            e = cpu_target - tc
            if tg is not None and prof.cpu_yields_to_gpu > 0:
                e = min(e, prof.cpu_yields_to_gpu * (gpu_target + YIELD_MARGIN_C - tg))
            self._i_cpu = _clamp(self._i_cpu + c.cpu_ki * _db(e) * dt, -(pl1_ceil - pl1_floor), 0.0)
            pl1 = _clamp(pl1_ceil + self._i_cpu + _p_term(c.cpu_kp, e), pl1_floor, pl1_ceil)
        if self._pl1_out is None or abs(pl1 - self._pl1_out) >= c.cpu_min_change_w or pl1 in (pl1_floor, pl1_ceil):
            self._pl1_out = pl1
        pl1 = self._pl1_out
        lo2, hi2 = cfg.limits.cpu_pl2_w
        pl2 = _clamp(max(pl1, prof.cpu_pl2_w * scale * (pl1 / pl1_ceil if pl1_ceil else 1.0)), lo2, hi2)
        pl2 = max(pl2, pl1)

        # ---- GPU clock loop ----
        g_floor, g_ceil, unlock_at_ceil = self._gpu_range(prof, scale)
        if tg is None:
            gclk = float(g_ceil)
        else:
            e = gpu_target - tg
            self._i_gpu = _clamp(self._i_gpu + c.gpu_ki * _db(e) * dt, -(g_ceil - g_floor), 0.0)
            gclk = _clamp(g_ceil + self._i_gpu + _p_term(c.gpu_kp, e), g_floor, g_ceil)
        step = c.gpu_clock_step_mhz
        gq = int(round(gclk / step) * step)
        gq = int(_clamp(gq, g_floor, g_ceil))
        if gq >= g_ceil - step and unlock_at_ceil:
            gpu_clock: int | None = None
        else:
            gpu_clock = gq
        # Asymmetric hysteresis: step down promptly when hot, step up only for a
        # clearly larger gain. Stops 1620<->1665 MHz ping-pong at steady state.
        prev = self._gpu_out
        if prev is not None:
            want_up = gpu_clock is None or gpu_clock > prev
            if want_up:
                if gpu_clock is not None and gpu_clock - prev < 2 * c.gpu_min_change_mhz:
                    gpu_clock = prev
                elif s.t - self._gpu_raised_at < RAISE_PERIOD_S:
                    gpu_clock = prev
                else:
                    target = g_ceil if gpu_clock is None else gpu_clock
                    stepped = min(target, prev + c.gpu_min_change_mhz)
                    gpu_clock = None if (gpu_clock is None and stepped >= g_ceil) else stepped
                    self._gpu_raised_at = s.t
            elif prev - gpu_clock < c.gpu_min_change_mhz:
                gpu_clock = prev
        self._gpu_out = gpu_clock

        # ---- GPU undervolt profile slot ----
        gpu_profile = prof.gpu_profile
        if band.prefer_efficiency and prof.gpu_profile_efficient is not None:
            gpu_profile = prof.gpu_profile_efficient

        # ---- fans: emergency boost with hysteresis ----
        over = max(
            (tc - cpu_target) if tc is not None else -99.0,
            (tg - gpu_target) if tg is not None else -99.0,
        )
        if over >= EMERGENCY_MARGIN_C:
            self._emergency = True
        elif over <= -EMERGENCY_CLEAR_C:
            self._emergency = False
        fan_boost = prof.fan_boost
        if self._emergency:
            fan_boost = cfg.limits.fan_boost[1]
            notes.append(f"emergency fan boost: {over:+.1f} °C over target")

        # ---- EPP: hotter rooms bias toward efficiency ----
        epp = int(_clamp(prof.cpu_epp + round((1.0 - scale) * 50), 0, 100))

        notes.append(
            f"ambient {est.ambient_c:.1f}±{est.std_c:.1f} °C ({est.source}{'' if trusted else ', not trusted yet'}),"
            f" band {band.name}"
        )
        return Decision(
            workload=workload,
            thermal_mode=prof.thermal_mode,
            cpu_pl1_w=round(pl1, 1),
            cpu_pl2_w=round(pl2, 1),
            cpu_epp=epp,
            cpu_boost=prof.cpu_boost,
            gpu_clock_max_mhz=gpu_clock,
            gpu_profile=gpu_profile,
            fan_boost=None if fan_boost is None else int(_clamp(fan_boost, *cfg.limits.fan_boost)),
            ambient_c=round(est.ambient_c, 1),
            band=band.name,
            notes=notes,
        )
