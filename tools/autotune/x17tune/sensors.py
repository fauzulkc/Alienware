"""Sensor sources: real (Windows/Linux) and simulated.

Everything optional degrades to None; the controller handles missing values.

Windows: CPU temperature/package power come from LibreHardwareMonitor's WMI
provider (namespace root\\LibreHardwareMonitor - run LHM in the background with
"Run on Windows startup"), with AWCC WMI as a CPU-temperature fallback.
GPU data comes from NVML (pip install nvidia-ml-py).
Linux: coretemp + RAPL energy counters (root) + NVML + alienware_wmi hwmon fans.
"""

from __future__ import annotations

import glob
import logging
import sys
import time
from pathlib import Path

import psutil

from .model import Sample

log = logging.getLogger("x17tune.sensors")

# NVML clock-event reasons that mean "slowed down because of heat".
_NVML_THERMAL_REASONS = 0x0000000000000020 | 0x0000000000000040 | 0x0000000000000008


class _Nvml:
    def __init__(self):
        self.h = None
        try:
            import pynvml  # type: ignore

            pynvml.nvmlInit()
            self.nv = pynvml
            self.h = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception as e:  # noqa: BLE001
            log.info("NVML unavailable (%s); GPU sensors disabled", e)

    def read(self) -> dict:
        if self.h is None:
            return {}
        nv, h, out = self.nv, self.h, {}
        for key, fn in (
            ("gpu_temp", lambda: float(nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU))),
            ("gpu_power", lambda: nv.nvmlDeviceGetPowerUsage(h) / 1000.0),
            ("gpu_util", lambda: float(nv.nvmlDeviceGetUtilizationRates(h).gpu)),
            ("gpu_clock", lambda: float(nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_GRAPHICS))),
            (
                "gpu_throttling",
                lambda: bool(nv.nvmlDeviceGetCurrentClocksThrottleReasons(h) & _NVML_THERMAL_REASONS),
            ),
        ):
            try:
                out[key] = fn()
            except Exception:  # noqa: BLE001
                pass
        return out


class _ProcCache:
    """Process names are expensive to list every second; refresh every 5 s."""

    def __init__(self, period_s: float = 5.0):
        self.period = period_s
        self.names: frozenset[str] = frozenset()
        self.t = -1e9

    def get(self) -> frozenset[str]:
        now = time.monotonic()
        if now - self.t >= self.period:
            names = set()
            for p in psutil.process_iter(["name"]):
                n = (p.info.get("name") or "").lower()
                if n:
                    names.add(n.removesuffix(".exe"))
            self.names, self.t = frozenset(names), now
        return self.names


def _on_ac() -> bool:
    try:
        b = psutil.sensors_battery()
    except Exception:  # noqa: BLE001
        return True
    return True if b is None or b.power_plugged is None else bool(b.power_plugged)


class SensorSource:
    def read(self) -> Sample:  # pragma: no cover - interface
        raise NotImplementedError


class LinuxSensors(SensorSource):
    def __init__(self):
        self.nvml = _Nvml()
        self.procs = _ProcCache()
        self._energy: tuple[float, int] | None = None
        self._coretemp = self._find_coretemp()
        self._fans = self._find_fans()
        psutil.cpu_percent(None)

    @staticmethod
    def _find_coretemp() -> str | None:
        for d in glob.glob("/sys/class/hwmon/hwmon*"):
            try:
                if Path(d, "name").read_text().strip() == "coretemp":
                    return f"{d}/temp1_input"  # Package id 0
            except OSError:
                continue
        return None

    @staticmethod
    def _find_fans() -> list[str]:
        from .actuators.linux import find_alienware_hwmon

        hw = find_alienware_hwmon()
        return sorted(glob.glob(f"{hw}/fan*_input")) if hw else []

    def _cpu_power(self, now: float) -> float | None:
        try:
            e = int(Path("/sys/class/powercap/intel-rapl:0/energy_uj").read_text())
        except (OSError, ValueError):
            return None
        prev, self._energy = self._energy, (now, e)
        if prev is None or now <= prev[0] or e < prev[1]:
            return None
        return (e - prev[1]) / 1e6 / (now - prev[0])

    def read(self) -> Sample:
        now = time.monotonic()
        temp = None
        if self._coretemp:
            try:
                temp = int(Path(self._coretemp).read_text()) / 1000.0
            except (OSError, ValueError):
                pass
        fans = []
        for f in self._fans:
            try:
                fans.append(int(Path(f).read_text()))
            except (OSError, ValueError):
                pass
        return Sample(
            t=now,
            cpu_temp=temp,
            cpu_power=self._cpu_power(now),
            cpu_util=psutil.cpu_percent(None),
            on_ac=_on_ac(),
            processes=self.procs.get(),
            fan_rpm=tuple(fans),
            **self.nvml.read(),
        )


class WindowsSensors(SensorSource):
    def __init__(self):
        self.nvml = _Nvml()
        self.procs = _ProcCache()
        self.lhm = None
        try:
            import wmi  # type: ignore

            self.lhm = wmi.WMI(namespace="root\\LibreHardwareMonitor")
            self.lhm.Sensor()  # probe
        except Exception as e:  # noqa: BLE001
            self.lhm = None
            log.warning(
                "LibreHardwareMonitor WMI not available (%s): CPU temp/power unknown, "
                "control falls back to open-loop for the CPU. Install & run LHM.",
                e,
            )
        psutil.cpu_percent(None)

    def _lhm(self) -> tuple[float | None, float | None]:
        if self.lhm is None:
            return None, None
        temp = power = None
        try:
            for s in self.lhm.Sensor():
                if s.SensorType == "Temperature" and s.Name in ("CPU Package", "Core Max") and "cpu" in s.Identifier:
                    temp = max(temp or 0.0, float(s.Value))
                elif s.SensorType == "Power" and s.Name == "CPU Package":
                    power = float(s.Value)
        except Exception as e:  # noqa: BLE001
            log.debug("LHM read failed: %s", e)
        return temp, power

    def read(self) -> Sample:
        from .actuators.windows import foreground_process

        fg, fg_path = foreground_process()
        temp, power = self._lhm()
        return Sample(
            t=time.monotonic(),
            cpu_temp=temp,
            cpu_power=power,
            cpu_util=psutil.cpu_percent(None),
            on_ac=_on_ac(),
            foreground=fg,
            foreground_path=fg_path,
            processes=self.procs.get(),
            **self.nvml.read(),
        )


class SimulatedSensors(SensorSource):
    """Wraps sim.ThermalPlant so `x17tune run --simulate` works anywhere."""

    def __init__(self, plant, workload_fn):
        self.plant = plant
        self.workload_fn = workload_fn
        self.pl1: float | None = None
        self.gpu_clock: float | None = None

    def feedback(self, decision) -> None:
        self.pl1 = decision.cpu_pl1_w
        self.gpu_clock = decision.gpu_clock_max_mhz

    def read(self) -> Sample:
        cpu_w, gpu_w, extra = self.workload_fn(self.plant.t)
        s = self.plant.step(1.0, cpu_w, gpu_w, pl1_w=self.pl1, gpu_clock_max=self.gpu_clock)
        for k, v in extra.items():
            setattr(s, k, v)
        return s


def for_platform() -> SensorSource:
    return WindowsSensors() if sys.platform.startswith("win") else LinuxSensors()
