"""Plain data types shared by sensors, classifier, controller and actuators."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Workload(str, enum.Enum):
    AI_INFERENCE = "ai_inference"
    AI_TRAINING = "ai_training"
    GAMING = "gaming"
    CREATOR = "creator"
    BALANCED = "balanced"
    QUIET = "quiet"
    BATTERY = "battery"


class ThermalMode(str, enum.Enum):
    """Generic names for the AWCC thermal profiles (see actuators/awcc.py)."""

    LOW_POWER = "low_power"
    QUIET = "quiet"
    COOL = "cool"
    BALANCED = "balanced"
    BALANCED_PERFORMANCE = "balanced_performance"
    PERFORMANCE = "performance"


@dataclass
class Sample:
    """One sensor reading. Any field may be None when the platform can't provide it."""

    t: float
    cpu_temp: float | None = None  # package / hottest core, °C
    cpu_power: float | None = None  # package power, W
    cpu_util: float = 0.0  # 0-100 %
    gpu_temp: float | None = None
    gpu_power: float | None = None
    gpu_util: float = 0.0
    gpu_clock: float | None = None  # MHz
    on_ac: bool = True
    foreground: str | None = None  # lower-case process name, without ".exe"
    foreground_path: str | None = None  # full executable path (lower-case) when known
    processes: frozenset[str] = frozenset()  # lower-case process names
    fan_rpm: tuple[int, ...] = ()
    cpu_throttling: bool = False
    gpu_throttling: bool = False


@dataclass
class Decision:
    """What the controller wants the machine to do this tick."""

    workload: Workload
    thermal_mode: ThermalMode
    cpu_pl1_w: float
    cpu_pl2_w: float
    cpu_epp: int  # 0 = max performance ... 100 = max power saving (Windows PERFEPP scale)
    cpu_boost: bool
    gpu_clock_max_mhz: int | None  # None = unlocked (driver default)
    gpu_profile: int | None  # MSI Afterburner profile slot (1-5) / None = leave as is
    fan_boost: int | None  # 0-255 extra fan boost, None = leave to EC
    ambient_c: float
    band: str
    notes: list[str] = field(default_factory=list)

    def key(self) -> tuple:
        """Fields whose change should trigger an actuation (notes excluded)."""
        return (
            self.thermal_mode,
            round(self.cpu_pl1_w),
            round(self.cpu_pl2_w),
            self.cpu_epp,
            self.cpu_boost,
            self.gpu_clock_max_mhz,
            self.gpu_profile,
            self.fan_boost,
        )
