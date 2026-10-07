"""Load and validate profiles.yaml / rules.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .model import ThermalMode, Workload


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Profile:
    thermal_mode: ThermalMode
    cpu_pl1_w: float
    cpu_pl1_floor_w: float
    cpu_pl2_w: float
    cpu_epp: int
    cpu_boost: bool
    gpu_clock_max_mhz: int | None
    gpu_clock_floor_mhz: int
    gpu_profile: int | None
    gpu_profile_efficient: int | None
    cpu_yields_to_gpu: float
    fan_boost: int | None


@dataclass(frozen=True)
class Band:
    name: str
    max_c: float
    power_scale: float
    temp_offset_c: float
    prefer_efficiency: bool


@dataclass(frozen=True)
class Limits:
    cpu_pl1_w: tuple[float, float]
    cpu_pl2_w: tuple[float, float]
    gpu_clock_mhz: tuple[int, int]
    fan_boost: tuple[int, int]


@dataclass(frozen=True)
class ControllerCfg:
    tick_s: float = 1.0
    smoothing_s: float = 5.0
    hold_s: float = 10.0
    cpu_kp: float = 0.8
    cpu_ki: float = 0.04
    gpu_kp: float = 15.0
    gpu_ki: float = 0.8
    gpu_clock_step_mhz: int = 15
    gpu_min_change_mhz: int = 45
    cpu_min_change_w: float = 2.0


@dataclass(frozen=True)
class Config:
    limits: Limits
    cpu_target_c: float
    gpu_target_c: float
    controller: ControllerCfg
    default_ambient_c: float
    hysteresis_c: float
    max_ambient_std_c: float
    bands: tuple[Band, ...]
    profiles: dict[Workload, Profile]
    rules: dict[str, Any] = field(default_factory=dict)


def _pair(v: Any, name: str) -> tuple[float, float]:
    if not (isinstance(v, (list, tuple)) and len(v) == 2 and v[0] <= v[1]):
        raise ConfigError(f"limits.{name} must be [min, max]")
    return (v[0], v[1])


def _profile(name: str, d: dict[str, Any], limits: Limits) -> Profile:
    try:
        p = Profile(
            thermal_mode=ThermalMode(d["thermal_mode"]),
            cpu_pl1_w=float(d["cpu_pl1_w"]),
            cpu_pl1_floor_w=float(d.get("cpu_pl1_floor_w", limits.cpu_pl1_w[0])),
            cpu_pl2_w=float(d["cpu_pl2_w"]),
            cpu_epp=int(d["cpu_epp"]),
            cpu_boost=bool(d["cpu_boost"]),
            gpu_clock_max_mhz=None if d.get("gpu_clock_max_mhz") is None else int(d["gpu_clock_max_mhz"]),
            gpu_clock_floor_mhz=int(d.get("gpu_clock_floor_mhz", limits.gpu_clock_mhz[0])),
            gpu_profile=d.get("gpu_profile"),
            gpu_profile_efficient=d.get("gpu_profile_efficient"),
            cpu_yields_to_gpu=float(d.get("cpu_yields_to_gpu", 0.5)),
            fan_boost=d.get("fan_boost"),
        )
    except (KeyError, ValueError, TypeError) as e:
        raise ConfigError(f"profile {name!r}: {e}") from e
    if not 0 <= p.cpu_epp <= 100:
        raise ConfigError(f"profile {name!r}: cpu_epp must be 0-100")
    if not limits.cpu_pl1_w[0] <= p.cpu_pl1_floor_w <= p.cpu_pl1_w:
        raise ConfigError(f"profile {name!r}: cpu_pl1_floor_w must be within [limits min, cpu_pl1_w]")
    if p.cpu_pl2_w < p.cpu_pl1_w:
        raise ConfigError(f"profile {name!r}: cpu_pl2_w must be >= cpu_pl1_w")
    for slot in (p.gpu_profile, p.gpu_profile_efficient):
        if slot is not None and not 1 <= int(slot) <= 5:
            raise ConfigError(f"profile {name!r}: Afterburner profile slots are 1-5")
    return p


def parse(profiles_doc: dict[str, Any], rules_doc: dict[str, Any] | None = None) -> Config:
    lim = profiles_doc.get("limits", {})
    limits = Limits(
        cpu_pl1_w=_pair(lim.get("cpu_pl1_w", [10, 90]), "cpu_pl1_w"),
        cpu_pl2_w=_pair(lim.get("cpu_pl2_w", [15, 115]), "cpu_pl2_w"),
        gpu_clock_mhz=tuple(int(x) for x in _pair(lim.get("gpu_clock_mhz", [600, 2100]), "gpu_clock_mhz")),
        fan_boost=tuple(int(x) for x in _pair(lim.get("fan_boost", [0, 255]), "fan_boost")),
    )
    targets = profiles_doc.get("targets", {})
    ctl = ControllerCfg(**profiles_doc.get("controller", {}))
    clim = profiles_doc.get("climate", {})
    bands = tuple(
        Band(
            name=str(b["name"]),
            max_c=float(b["max_c"]),
            power_scale=float(b["power_scale"]),
            temp_offset_c=float(b.get("temp_offset_c", 0)),
            prefer_efficiency=bool(b.get("prefer_efficiency", False)),
        )
        for b in clim.get("bands", [])
    )
    if not bands:
        raise ConfigError("climate.bands must not be empty")
    if any(a.max_c >= b.max_c for a, b in zip(bands, bands[1:])):
        raise ConfigError("climate.bands must be sorted by ascending max_c")
    for b in bands:
        if not 0.3 <= b.power_scale <= 1.2:
            raise ConfigError(f"band {b.name}: power_scale out of sane range 0.3-1.2")

    profs: dict[Workload, Profile] = {}
    for w in Workload:
        if w.value not in profiles_doc.get("profiles", {}):
            raise ConfigError(f"missing profile for workload {w.value!r}")
        profs[w] = _profile(w.value, profiles_doc["profiles"][w.value], limits)

    return Config(
        limits=limits,
        cpu_target_c=float(targets.get("cpu_temp_c", 90)),
        gpu_target_c=float(targets.get("gpu_temp_c", 80)),
        controller=ctl,
        default_ambient_c=float(clim.get("default_ambient_c", 32)),
        hysteresis_c=float(clim.get("hysteresis_c", 1.0)),
        max_ambient_std_c=float(clim.get("max_ambient_std_c", 2.0)),
        bands=bands,
        profiles=profs,
        rules=rules_doc or {},
    )


def _read_yaml(path: Path | None, packaged: str) -> dict[str, Any]:
    if path is not None:
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = resources.files("x17tune").joinpath("config", packaged).read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def load(profiles_path: Path | None = None, rules_path: Path | None = None) -> Config:
    """Load config; None paths fall back to the packaged defaults."""
    return parse(_read_yaml(profiles_path, "profiles.yaml"), _read_yaml(rules_path, "rules.yaml"))
