"""A small thermal simulator of the x17 R2 used by tests and `x17tune simulate`.

Two heatsink nodes (CPU side, GPU side) coupled through shared heat-pipes,
each with a fast die-to-heatsink rise. Constants are rough fits to published
x17 R2 reviews (12700H ~45 W sustained around 90 °C, 3070 Ti ~140 W around
80 °C in a ~25 °C room, fans near max). It is *not* a model of your exact unit;
it exists so the estimator and controller can be tested end-to-end.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .model import Decision, Sample


@dataclass
class PlantParams:
    r_cpu_hs: float = 0.95  # °C/W heatsink -> air, CPU side (fans high)
    r_gpu_hs: float = 0.32
    r_couple: float = 1.2  # °C/W between the two heatsink halves
    c_cpu_hs: float = 80.0  # J/°C
    c_gpu_hs: float = 200.0
    r_cpu_die: float = 0.32  # °C/W die -> heatsink
    r_gpu_die: float = 0.06
    noise_c: float = 0.4
    gpu_ref_clock: float = 1800.0


@dataclass
class ThermalPlant:
    ambient_c: float = 30.0
    params: PlantParams = field(default_factory=PlantParams)
    seed: int = 0

    def __post_init__(self):
        self.t = 0.0
        self.t_cpu_hs = self.ambient_c + 5
        self.t_gpu_hs = self.ambient_c + 5
        self.rng = random.Random(self.seed)
        self.pc = 0.0
        self.pg = 0.0

    def die_temps(self) -> tuple[float, float]:
        p = self.params
        return self.t_cpu_hs + p.r_cpu_die * self.pc, self.t_gpu_hs + p.r_gpu_die * self.pg

    def step(
        self,
        dt: float,
        cpu_demand_w: float,
        gpu_demand_w: float,
        pl1_w: float | None = None,
        gpu_clock_max: float | None = None,
    ) -> Sample:
        p = self.params
        pc = cpu_demand_w if pl1_w is None else min(cpu_demand_w, pl1_w)
        pg = gpu_demand_w
        if gpu_clock_max is not None and gpu_clock_max < p.gpu_ref_clock:
            # Power ~ f·V²; along the V/F curve that is roughly f^2.2.
            pg = gpu_demand_w * (gpu_clock_max / p.gpu_ref_clock) ** 2.2
        tc_die, tg_die = self.t_cpu_hs + p.r_cpu_die * pc, self.t_gpu_hs + p.r_gpu_die * pg
        cpu_thr = tc_die >= 100.0
        gpu_thr = tg_die >= 87.0
        if cpu_thr:
            pc *= 0.7
        if gpu_thr:
            pg *= 0.8
        self.pc, self.pg = pc, pg

        q_c_air = (self.t_cpu_hs - self.ambient_c) / p.r_cpu_hs
        q_g_air = (self.t_gpu_hs - self.ambient_c) / p.r_gpu_hs
        q_cg = (self.t_cpu_hs - self.t_gpu_hs) / p.r_couple
        self.t_cpu_hs += dt * (pc - q_c_air - q_cg) / p.c_cpu_hs
        self.t_gpu_hs += dt * (pg - q_g_air + q_cg) / p.c_gpu_hs
        self.t += dt

        tc, tg = self.die_temps()
        n = p.noise_c
        return Sample(
            t=self.t,
            cpu_temp=tc + self.rng.gauss(0, n),
            cpu_power=pc,
            cpu_util=min(100.0, pc * 1.6),
            gpu_temp=tg + self.rng.gauss(0, n),
            gpu_power=pg,
            gpu_util=min(100.0, pg / 1.4),
            gpu_clock=gpu_clock_max or p.gpu_ref_clock,
            cpu_throttling=cpu_thr,
            gpu_throttling=gpu_thr,
        )


def run_closed_loop(plant: ThermalPlant, controller, workload_fn, seconds: int, dt: float = 1.0):
    """Drive `plant` with `controller` for `seconds`. `workload_fn(t)` -> (cpu_W, gpu_W, Sample overrides)."""
    decision: Decision | None = None
    out = []
    for _ in range(int(seconds / dt)):
        cpu_w, gpu_w, extra = workload_fn(plant.t)
        s = plant.step(
            dt,
            cpu_w,
            gpu_w,
            pl1_w=decision.cpu_pl1_w if decision else None,
            gpu_clock_max=decision.gpu_clock_max_mhz if decision else None,
        )
        for k, v in extra.items():
            setattr(s, k, v)
        decision = controller.step(s)
        out.append((s, decision))
    return out
