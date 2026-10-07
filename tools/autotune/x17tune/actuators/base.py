"""Actuator plumbing shared by the Windows and Linux back-ends.

All side effects go through a `Runner` (commands) and `write_file` (sysfs), so
the same actuator code runs in dry-run mode, where every action is recorded
instead of executed. Tests use that to check the exact commands emitted.
"""

from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from ..model import Decision, Sample, ThermalMode

log = logging.getLogger("x17tune.actuator")


class Runner:
    """Executes commands and file writes for real."""

    dry = False

    def run(self, argv: list[str], timeout: float = 15.0) -> str:
        log.debug("run: %s", argv)
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"{argv[0]} failed ({res.returncode}): {res.stderr.strip() or res.stdout.strip()}")
        return res.stdout

    def write(self, path: str | Path, value: str) -> None:
        log.debug("write: %s <- %s", path, value)
        Path(path).write_text(value)


class DryRunner(Runner):
    """Records what would happen. Reads still work so discovery is realistic."""

    dry = True

    def __init__(self):
        self.actions: list[str] = []

    def run(self, argv: list[str], timeout: float = 15.0) -> str:
        self.actions.append("RUN " + " ".join(argv))
        return ""

    def write(self, path: str | Path, value: str) -> None:
        self.actions.append(f"WRITE {path} = {value}")


class Actuator(ABC):
    """Applies Decisions, touching only what changed since the last apply."""

    name = "base"

    def __init__(self, runner: Runner | None = None):
        self.runner = runner or DryRunner()
        self.last: Decision | None = None

    # Back-end hooks ------------------------------------------------------
    @abstractmethod
    def set_thermal_mode(self, mode: ThermalMode) -> None: ...

    @abstractmethod
    def set_cpu(self, d: Decision, s: Sample, changed: bool) -> None:
        """Called every tick (back-ends may run inner loops); `changed` = CPU fields differ from last apply."""

    @abstractmethod
    def set_gpu_clock(self, max_mhz: int | None) -> None: ...

    @abstractmethod
    def set_gpu_profile(self, slot: int) -> None: ...

    @abstractmethod
    def set_fan_boost(self, boost: int) -> None: ...

    @abstractmethod
    def restore(self) -> None:
        """Return the machine to safe stock-like behavior."""

    # ---------------------------------------------------------------------
    def apply(self, d: Decision, s: Sample) -> None:
        p = self.last
        if p is None or p.thermal_mode != d.thermal_mode:
            self.set_thermal_mode(d.thermal_mode)
        cpu_changed = p is None or (p.cpu_pl1_w, p.cpu_pl2_w, p.cpu_epp, p.cpu_boost) != (
            d.cpu_pl1_w,
            d.cpu_pl2_w,
            d.cpu_epp,
            d.cpu_boost,
        )
        self.set_cpu(d, s, cpu_changed)
        if p is None or p.gpu_clock_max_mhz != d.gpu_clock_max_mhz:
            self.set_gpu_clock(d.gpu_clock_max_mhz)
        if d.gpu_profile is not None and (p is None or p.gpu_profile != d.gpu_profile):
            self.set_gpu_profile(d.gpu_profile)
        if d.fan_boost is not None and (p is None or p.fan_boost != d.fan_boost):
            self.set_fan_boost(d.fan_boost)
        elif d.fan_boost is None and p is not None and p.fan_boost not in (None, 0):
            self.set_fan_boost(0)  # hand the fans back to the EC
        self.last = d


def nvidia_smi_clock_args(max_mhz: int | None) -> list[str]:
    if max_mhz is None:
        return ["nvidia-smi", "-rgc"]
    return ["nvidia-smi", "-lgc", f"210,{int(max_mhz)}"]
