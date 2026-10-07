"""Linux back-end (kernel 6.15+ recommended for alienware-wmi platform_profile + fan boost).

* Thermal mode - /sys/firmware/acpi/platform_profile (alienware-wmi driver).
* Fan boost    - hwmon "alienware_wmi" fanN_boost (0-255).
* CPU          - intel_rapl powercap PL1/PL2 (both MSR and MMIO interfaces; the
                 lower one wins in hardware), intel_pstate EPP and no_turbo.
* GPU          - nvidia-smi -lgc/-rgc; optional clock offsets per "profile slot"
                 through nvidia-settings (X11 + Coolbits only).

Original values are snapshotted on first apply and written back by restore().
Run as root (see install/linux/x17tune.service).
"""

from __future__ import annotations

import glob
import logging
from pathlib import Path

from ..model import Decision, Sample, ThermalMode
from .base import Actuator, Runner, nvidia_smi_clock_args

log = logging.getLogger("x17tune.linux")

PLATFORM_PROFILE = "/sys/firmware/acpi/platform_profile"
PLATFORM_CHOICES = "/sys/firmware/acpi/platform_profile_choices"
NO_TURBO = "/sys/devices/system/cpu/intel_pstate/no_turbo"
RAPL_GLOBS = ("/sys/class/powercap/intel-rapl:0", "/sys/class/powercap/intel-rapl-mmio:0")

MODE_NAMES = {
    ThermalMode.LOW_POWER: ["low-power", "quiet", "balanced"],
    ThermalMode.QUIET: ["quiet", "low-power", "balanced"],
    ThermalMode.COOL: ["cool", "quiet", "balanced"],
    ThermalMode.BALANCED: ["balanced"],
    ThermalMode.BALANCED_PERFORMANCE: ["balanced-performance", "performance", "balanced"],
    ThermalMode.PERFORMANCE: ["performance", "balanced-performance", "balanced"],
}


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def find_alienware_hwmon(root: str = "/sys/class/hwmon") -> str | None:
    for d in sorted(glob.glob(f"{root}/hwmon*")):
        if _read(f"{d}/name") == "alienware_wmi":
            return d
    return None


class LinuxActuator(Actuator):
    name = "linux"

    def __init__(self, runner: Runner | None = None, gpu_offsets: dict[int, int] | None = None, sysfs_root: str = ""):
        super().__init__(runner)
        self.root = sysfs_root  # tests point this at a fake tree
        self.gpu_offsets = gpu_offsets or {}
        self._orig: dict[str, str] = {}

    def _p(self, path: str) -> str:
        return f"{self.root}{path}"

    def _write(self, path: str, value: str) -> None:
        full = self._p(path)
        if full not in self._orig:
            cur = _read(full)
            if cur is not None:
                self._orig[full] = cur
        self.runner.write(full, value)

    # -- thermal / fans -----------------------------------------------------
    def set_thermal_mode(self, mode: ThermalMode) -> None:
        choices = (_read(self._p(PLATFORM_CHOICES)) or "").split()
        if not choices:
            log.info("platform_profile not available (alienware-wmi not loaded?)")
            return
        for name in MODE_NAMES[mode]:
            if name in choices:
                self._write(PLATFORM_PROFILE, name)
                return

    def set_fan_boost(self, boost: int) -> None:
        hw = find_alienware_hwmon(self._p("/sys/class/hwmon"))
        if hw is None:
            return
        for f in sorted(glob.glob(f"{hw}/fan*_boost")):
            rel = f[len(self.root) :] if self.root else f
            self._write(rel, str(max(0, min(255, boost))))

    # -- CPU ------------------------------------------------------------------
    def _rapl_dirs(self) -> list[str]:
        return [g for g in RAPL_GLOBS if Path(self._p(g)).exists()]

    def set_cpu(self, d: Decision, s: Sample, changed: bool) -> None:
        if not changed:
            return
        for rapl in self._rapl_dirs():
            self._write(f"{rapl}/constraint_0_power_limit_uw", str(int(d.cpu_pl1_w * 1e6)))
            if Path(self._p(f"{rapl}/constraint_1_power_limit_uw")).exists():
                self._write(f"{rapl}/constraint_1_power_limit_uw", str(int(d.cpu_pl2_w * 1e6)))
        epp255 = str(round(d.cpu_epp * 2.55))
        for f in sorted(glob.glob(self._p("/sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference"))):
            self._write(f[len(self.root) :] if self.root else f, epp255)
        if Path(self._p(NO_TURBO)).exists():
            self._write(NO_TURBO, "0" if d.cpu_boost else "1")

    # -- GPU ------------------------------------------------------------------
    def set_gpu_clock(self, max_mhz: int | None) -> None:
        self.runner.run(nvidia_smi_clock_args(max_mhz))

    def set_gpu_profile(self, slot: int) -> None:
        off = self.gpu_offsets.get(int(slot))
        if off is None:
            return
        self.runner.run(["nvidia-settings", "-a", f"[gpu:0]/GPUGraphicsClockOffsetAllPerformanceLevels={int(off)}"])

    # -- safety ---------------------------------------------------------------
    def restore(self) -> None:
        for path, val in list(self._orig.items()):
            try:
                self.runner.write(path, val)
            except Exception as e:  # noqa: BLE001
                log.error("restore %s failed: %s", path, e)
        try:
            self.set_gpu_clock(None)
        except Exception as e:  # noqa: BLE001
            log.error("GPU clock reset failed: %s", e)
        self.last = None
