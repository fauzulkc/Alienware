"""Windows 11 back-end.

Levers available on the x17 R2 under Windows without kernel drivers:

* AWCC thermal profile + fan boost - WMI `AWCCWmiMethodFunction.Thermal_Control`
  (same calls Alienware Command Center makes). Called through PowerShell CIM.
* CPU - power-plan values (`powercfg`): EPP (PERFEPP), boost mode
  (PERFBOOSTMODE) and a max-frequency cap per core class (PROCFREQMAX for
  P-cores, PROCFREQMAX1 for E-cores). Windows cannot set PL1/PL2 directly, so
  an inner loop moves the frequency cap to hold measured package power at the
  controller's PL1 target.
* GPU - `nvidia-smi -lgc/-rgc` (admin) for clock ceilings, and MSI Afterburner
  profile slots (`MSIAfterburner.exe -ProfileN`) for user-made V/F-curve
  undervolts.

Run elevated (Task Scheduler "highest privileges" - see install/windows).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..model import Decision, Sample, ThermalMode
from . import awcc
from .base import Actuator, Runner, nvidia_smi_clock_args

log = logging.getLogger("x17tune.windows")

AFTERBURNER = r"C:\Program Files (x86)\MSI Afterburner\MSIAfterburner.exe"

P_CORE_MAX_MHZ = 4700  # i7-12700H max turbo
E_CORE_RATIO = 3500 / 4700  # E-core max turbo relative to P-core
FREQ_CAP_MIN_MHZ = 1400
FREQ_STEP_MHZ = 100
FREQ_LOOP_PERIOD_S = 3.0


def _ps(script: str) -> list[str]:
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]


class WindowsActuator(Actuator):
    name = "windows"

    def __init__(
        self,
        runner: Runner | None = None,
        awcc_table: str = "ustt",
        awcc_arg_name: str = "arg2",
        fan_ids: tuple[int, ...] = (awcc.FAN_CPU_1, awcc.FAN_GPU_1),
        afterburner: str = AFTERBURNER,
    ):
        super().__init__(runner)
        self.table = awcc_table
        self.arg_name = awcc_arg_name
        self.fan_ids = fan_ids
        self.afterburner = afterburner
        self.freq_cap = 0  # 0 = no cap (Windows semantics)
        self._last_freq_change = -1e9
        self._original: dict[str, tuple[int | None, int | None]] = {}
        self._policy: tuple | None = None

    # -- AWCC -------------------------------------------------------------
    def _thermal_control(self, value: int) -> None:
        script = (
            "$o = Get-CimInstance -Namespace root\\WMI -ClassName AWCCWmiMethodFunction; "
            f"Invoke-CimMethod -InputObject $o -MethodName Thermal_Control -Arguments @{{{self.arg_name}=[uint32]{value}}}"
        )
        self.runner.run(_ps(script))

    def set_thermal_mode(self, mode: ThermalMode) -> None:
        code = awcc.profile_code(mode, self.table)
        self._thermal_control(awcc.pack(awcc.OP_ACTIVATE_PROFILE, code))

    def set_fan_boost(self, boost: int) -> None:
        for fid in self.fan_ids:
            self._thermal_control(awcc.pack(awcc.OP_SET_FAN_BOOST, fid, max(0, min(255, boost))))

    # -- CPU (power plan) -------------------------------------------------
    def _powercfg_set(self, alias: str, value: int, on_ac: bool) -> None:
        verb = "/setacvalueindex" if on_ac else "/setdcvalueindex"
        self.runner.run(["powercfg", verb, "SCHEME_CURRENT", "SUB_PROCESSOR", alias, str(int(value))])

    def _powercfg_commit(self) -> None:
        self.runner.run(["powercfg", "/setactive", "SCHEME_CURRENT"])

    def snapshot_power_plan(self) -> None:
        """Remember the user's current values so restore() can put them back."""
        for alias in ("PERFEPP", "PERFBOOSTMODE", "PROCFREQMAX", "PROCFREQMAX1"):
            try:
                out = self.runner.run(["powercfg", "/q", "SCHEME_CURRENT", "SUB_PROCESSOR", alias])
            except Exception as e:  # noqa: BLE001 - best effort
                log.warning("could not read %s: %s", alias, e)
                continue
            self._original[alias] = parse_powercfg_indices(out)

    def _freq_inner_loop(self, d: Decision, s: Sample) -> bool:
        """Adjust the P-core frequency cap so package power tracks PL1. Returns True if changed."""
        if s.t - self._last_freq_change < FREQ_LOOP_PERIOD_S:
            return False
        cap = self.freq_cap or P_CORE_MAX_MHZ
        if s.cpu_power is None:
            # No power sensor (LibreHardwareMonitor not running): map PL1 to a cap.
            # Package power ~ f^2.5 on Alder Lake's V/F curve around 2-4.7 GHz.
            new = P_CORE_MAX_MHZ * min(1.0, (d.cpu_pl1_w / 90.0) ** 0.4)
        elif s.cpu_power > d.cpu_pl1_w + 2:
            new = cap - FREQ_STEP_MHZ
        elif s.cpu_power < d.cpu_pl1_w - 5:
            new = cap + FREQ_STEP_MHZ
        else:
            return False
        new = int(round(max(FREQ_CAP_MIN_MHZ, min(P_CORE_MAX_MHZ, new)) / FREQ_STEP_MHZ) * FREQ_STEP_MHZ)
        new_cap = 0 if new >= P_CORE_MAX_MHZ else new
        if new_cap == self.freq_cap:
            return False
        self.freq_cap = new_cap
        self._last_freq_change = s.t
        self._powercfg_set("PROCFREQMAX", new_cap, s.on_ac)
        self._powercfg_set("PROCFREQMAX1", 0 if new_cap == 0 else int(new_cap * E_CORE_RATIO), s.on_ac)
        return True

    def set_cpu(self, d: Decision, s: Sample, changed: bool) -> None:
        dirty = False
        policy = (d.cpu_epp, d.cpu_boost, s.on_ac)
        if changed and policy != self._policy:
            self._powercfg_set("PERFEPP", d.cpu_epp, s.on_ac)
            self._powercfg_set("PERFBOOSTMODE", 2 if d.cpu_boost else 0, s.on_ac)  # 2 = aggressive
            self._policy = policy
            dirty = True
        dirty |= self._freq_inner_loop(d, s)
        if dirty:
            self._powercfg_commit()

    # -- GPU ----------------------------------------------------------------
    def set_gpu_clock(self, max_mhz: int | None) -> None:
        self.runner.run(nvidia_smi_clock_args(max_mhz))

    def set_gpu_profile(self, slot: int) -> None:
        if not self.runner.dry and not Path(self.afterburner).exists():
            log.info("MSI Afterburner not found; skipping GPU profile %s", slot)
            return
        self.runner.run([self.afterburner, f"-Profile{int(slot)}"])

    # -- safety ---------------------------------------------------------------
    def restore(self) -> None:
        steps = [
            lambda: self.set_thermal_mode(ThermalMode.BALANCED),
            lambda: self.set_fan_boost(0),
            lambda: self.set_gpu_clock(None),
        ]
        for alias, default in (("PERFEPP", 33), ("PERFBOOSTMODE", 2), ("PROCFREQMAX", 0), ("PROCFREQMAX1", 0)):
            ac, dc = self._original.get(alias, (default, default))
            steps.append(lambda a=alias, v=ac, d=default: self._powercfg_set(a, d if v is None else v, True))
            steps.append(lambda a=alias, v=dc, d=default: self._powercfg_set(a, d if v is None else v, False))
        steps.append(self._powercfg_commit)
        for fn in steps:
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - restore must try everything
                log.error("restore step failed: %s", e)
        self.freq_cap = 0
        self._policy = None
        self.last = None


_IDX = re.compile(r"Current (AC|DC) Power Setting Index:\s*0x([0-9a-fA-F]+)")


def parse_powercfg_indices(text: str) -> tuple[int | None, int | None]:
    ac = dc = None
    for kind, val in _IDX.findall(text):
        if kind == "AC":
            ac = int(val, 16)
        else:
            dc = int(val, 16)
    return ac, dc


def foreground_process() -> tuple[str | None, str | None]:
    """(name without .exe, full path) of the foreground window's process."""
    try:
        import ctypes
        from ctypes import wintypes

        import psutil

        hwnd = ctypes.windll.user32.GetForegroundWindow()  # type: ignore[attr-defined]
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))  # type: ignore[attr-defined]
        p = psutil.Process(pid.value)
        return p.name().lower().removesuffix(".exe"), (p.exe() or "").lower()
    except Exception:  # noqa: BLE001
        return None, None
