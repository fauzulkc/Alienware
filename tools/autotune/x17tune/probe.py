"""`x17tune probe` - read-only report of which levers this machine exposes."""

from __future__ import annotations

import glob
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .actuators import awcc

# PowerShell: list AWCC methods and run read-only Thermal_Information queries.
_PS_AWCC = r"""
$ErrorActionPreference = 'Stop'
$out = [ordered]@{}
try {
  $cls = Get-CimClass -Namespace root\WMI -ClassName AWCCWmiMethodFunction
  $out.methods = @($cls.CimClassMethods | ForEach-Object {
    [ordered]@{ name = $_.Name; params = @($_.Parameters | ForEach-Object { "$($_.Name):$($_.CimType)" }) } })
  $o = Get-CimInstance -Namespace root\WMI -ClassName AWCCWmiMethodFunction
  $argName = (($cls.CimClassMethods['Thermal_Information'].Parameters |
               Where-Object { $_.Qualifiers.Name -contains 'In' }) | Select-Object -First 1).Name
  $out.arg_name = $argName
  function Q([uint32]$v) {
    $r = Invoke-CimMethod -InputObject $o -MethodName Thermal_Information -Arguments @{ $argName = $v }
    ($r.CimInstanceProperties | Where-Object { $_.Name -ne 'ReturnValue' -and $_.Name -ne 'PSComputerName' } |
      Select-Object -First 1).Value
  }
  $desc = [uint32](Q 0x02)
  $out.system_description = $desc
  $fans = $desc -band 0xFF; $temps = ($desc -shr 8) -band 0xFF
  $unk = ($desc -shr 16) -band 0xFF; $profs = ($desc -shr 24) -band 0xFF
  $ids = @(); for ($i = 0; $i -lt ($fans + $temps + $unk + $profs); $i++) { $ids += ([uint32](Q (0x03 -bor ($i -shl 8)))) -band 0xFF }
  $out.fan_ids = @($ids | Select-Object -First $fans)
  $out.sensor_ids = @($ids | Select-Object -Skip $fans -First $temps)
  $out.profile_ids = @($ids | Select-Object -Skip ($fans + $temps + $unk) -First $profs)
  $out.current_profile = [uint32](Q 0x0B)
  $out.fan_rpm = @($out.fan_ids | ForEach-Object { [uint32](Q (0x05 -bor ($_ -shl 8))) })
  $out.fan_boost = @($out.fan_ids | ForEach-Object { [uint32](Q (0x0C -bor ($_ -shl 8))) })
  $out.temps = @($out.sensor_ids | ForEach-Object { [uint32](Q (0x04 -bor ($_ -shl 8))) })
} catch { $out.error = "$_" }
$out | ConvertTo-Json -Depth 5
"""


def _run(argv: list[str], timeout: float = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        return r.returncode, (r.stdout or r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def _read(p: str) -> str | None:
    try:
        return Path(p).read_text().strip()
    except OSError:
        return None


def probe() -> dict:
    rep: dict = {"platform": platform.platform(), "python": sys.version.split()[0]}
    rep["nvidia_smi"] = shutil.which("nvidia-smi") is not None
    if rep["nvidia_smi"]:
        rc, out = _run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,vbios_version,power.default_limit,power.max_limit,"
                "clocks.max.graphics,pcie.link.gen.max",
                "--format=csv,noheader",
            ]
        )
        rep["gpu"] = out if rc == 0 else f"error: {out}"
    try:
        import pynvml  # type: ignore  # noqa: F401

        rep["nvml_python"] = True
    except ImportError:
        rep["nvml_python"] = False

    if sys.platform.startswith("win"):
        rc, out = _run(["powershell", "-NoProfile", "-Command", _PS_AWCC], timeout=60)
        try:
            a = json.loads(out)
            for k in ("profile_ids",):
                if isinstance(a.get(k), list):
                    a[k + "_decoded"] = [awcc.PROFILE_NAMES.get(int(x), hex(int(x))) for x in a[k]]
            if a.get("current_profile") is not None:
                a["current_profile_decoded"] = awcc.PROFILE_NAMES.get(int(a["current_profile"]) & 0xFF)
            rep["awcc"] = a
        except ValueError:
            rep["awcc"] = {"error": out[:500]}
        rc, out = _run(
            ["powershell", "-NoProfile", "-Command", "Get-CimInstance -Namespace root\\LibreHardwareMonitor -ClassName Sensor | Measure-Object | % Count"]
        )
        rep["librehardwaremonitor_wmi"] = rc == 0 and out.strip().isdigit() and int(out.strip()) > 0
        from .actuators.windows import AFTERBURNER

        rep["msi_afterburner"] = Path(AFTERBURNER).exists()
        rc, out = _run(["powercfg", "/q", "SCHEME_CURRENT", "SUB_PROCESSOR", "PROCFREQMAX"])
        rep["powercfg_procfreqmax"] = rc == 0
    else:
        rep["platform_profile_choices"] = _read("/sys/firmware/acpi/platform_profile_choices")
        rep["platform_profile"] = _read("/sys/firmware/acpi/platform_profile")
        rep["rapl"] = {
            d: {
                "pl1_w": (int(_read(f"{d}/constraint_0_power_limit_uw") or 0) / 1e6) or None,
                "pl2_w": (int(_read(f"{d}/constraint_1_power_limit_uw") or 0) / 1e6) or None,
            }
            for d in sorted(glob.glob("/sys/class/powercap/intel-rapl*:0"))
        }
        from .actuators.linux import find_alienware_hwmon

        hw = find_alienware_hwmon()
        rep["alienware_hwmon"] = hw
        if hw:
            rep["fan_boost_files"] = sorted(Path(p).name for p in glob.glob(f"{hw}/fan*_boost"))
        rep["epp"] = _read("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")
        rep["no_turbo"] = _read("/sys/devices/system/cpu/intel_pstate/no_turbo")
    return rep
