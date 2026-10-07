from pathlib import Path

import pytest

from x17tune.actuators import awcc
from x17tune.actuators.base import DryRunner
from x17tune.actuators.linux import LinuxActuator
from x17tune.actuators.windows import WindowsActuator, parse_powercfg_indices
from x17tune.model import Decision, Sample, ThermalMode, Workload


def decision(**kw):
    base = dict(
        workload=Workload.GAMING,
        thermal_mode=ThermalMode.PERFORMANCE,
        cpu_pl1_w=45.0,
        cpu_pl2_w=90.0,
        cpu_epp=25,
        cpu_boost=True,
        gpu_clock_max_mhz=None,
        gpu_profile=2,
        fan_boost=None,
        ambient_c=32.0,
        band="warm",
    )
    base.update(kw)
    return Decision(**base)


def test_awcc_packing_matches_linux_driver():
    assert awcc.pack(awcc.OP_ACTIVATE_PROFILE, 0xA4) == 0xA401
    assert awcc.pack(awcc.OP_SET_FAN_BOOST, awcc.FAN_GPU_1, 255) == 0xFF3302
    with pytest.raises(ValueError):
        awcc.pack(0x100)
    assert awcc.profile_code(ThermalMode.COOL, "legacy") == 0x96  # falls back to quiet
    assert awcc.profile_code(ThermalMode.LOW_POWER, "ustt") == 0xA5


def test_windows_emits_expected_commands():
    r = DryRunner()
    act = WindowsActuator(r, afterburner="AB.exe")
    act.apply(decision(), Sample(t=0, cpu_power=45))
    joined = "\n".join(r.actions)
    assert f"arg2=[uint32]{0xA401}" in joined  # USTT performance
    assert "PERFEPP 25" in joined and "PERFBOOSTMODE 2" in joined
    assert "nvidia-smi -rgc" in joined
    assert "AB.exe -Profile2" in joined
    assert "/setactive SCHEME_CURRENT" in joined


def test_windows_only_touches_what_changed():
    r = DryRunner()
    act = WindowsActuator(r, afterburner="AB.exe")
    act.apply(decision(), Sample(t=0, cpu_power=45))
    r.actions.clear()
    act.apply(decision(gpu_clock_max_mhz=1605), Sample(t=1, cpu_power=45))
    assert r.actions == ["RUN nvidia-smi -lgc 210,1605"]


def test_windows_frequency_cap_tracks_pl1():
    r = DryRunner()
    act = WindowsActuator(r)
    d = decision(cpu_pl1_w=30)
    act.apply(d, Sample(t=0, cpu_power=45))  # over PL1 -> cap engages
    assert act.freq_cap == 4600
    act.apply(d, Sample(t=1, cpu_power=45))  # within loop period -> no change
    assert act.freq_cap == 4600
    act.apply(d, Sample(t=4, cpu_power=45))
    assert act.freq_cap == 4500
    assert any("PROCFREQMAX1" in a for a in r.actions)
    act.apply(d, Sample(t=8, cpu_power=10))  # well under -> relax
    assert act.freq_cap == 4600


def test_windows_fan_boost_and_release():
    r = DryRunner()
    act = WindowsActuator(r, fan_ids=(0x32, 0x33))
    act.apply(decision(fan_boost=255), Sample(t=0))
    assert sum(f"[uint32]{awcc.pack(2, 0x32, 255)}" in a for a in r.actions) == 1
    r.actions.clear()
    act.apply(decision(fan_boost=None), Sample(t=1))
    assert any(f"[uint32]{awcc.pack(2, 0x33, 0)}" in a for a in r.actions)


def test_windows_restore_uses_snapshot():
    r = DryRunner()
    act = WindowsActuator(r)
    act._original = {"PERFEPP": (40, 60)}
    act.restore()
    assert "RUN powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PERFEPP 40" in r.actions
    assert "RUN powercfg /setdcvalueindex SCHEME_CURRENT SUB_PROCESSOR PERFEPP 60" in r.actions
    assert "RUN powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCFREQMAX 0" in r.actions
    assert f"arg2=[uint32]{0xA001}" in "\n".join(r.actions)  # balanced


def test_parse_powercfg():
    text = """    Current AC Power Setting Index: 0x00000021
    Current DC Power Setting Index: 0x00000032"""
    assert parse_powercfg_indices(text) == (33, 50)


@pytest.fixture
def fake_sysfs(tmp_path: Path):
    def w(rel, val):
        p = tmp_path / rel.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(val)

    w("/sys/firmware/acpi/platform_profile_choices", "low-power quiet cool balanced balanced-performance performance")
    w("/sys/firmware/acpi/platform_profile", "balanced")
    w("/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw", "45000000")
    w("/sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw", "115000000")
    w("/sys/class/powercap/intel-rapl-mmio:0/constraint_0_power_limit_uw", "45000000")
    w("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference", "balance_performance")
    w("/sys/devices/system/cpu/cpu1/cpufreq/energy_performance_preference", "balance_performance")
    w("/sys/devices/system/cpu/intel_pstate/no_turbo", "0")
    w("/sys/class/hwmon/hwmon3/name", "alienware_wmi")
    w("/sys/class/hwmon/hwmon3/fan1_boost", "0")
    w("/sys/class/hwmon/hwmon3/fan2_boost", "0")
    return tmp_path


def test_linux_writes_and_restores(fake_sysfs):
    from x17tune.actuators.base import Runner

    act = LinuxActuator(Runner(), sysfs_root=str(fake_sysfs))
    act.runner.run = lambda argv, timeout=15: ""  # no nvidia-smi here

    def rd(rel):
        return (fake_sysfs / rel.lstrip("/")).read_text()

    act.apply(decision(cpu_pl1_w=30, cpu_pl2_w=60, cpu_epp=40, cpu_boost=False, fan_boost=128), Sample(t=0))
    assert rd("/sys/firmware/acpi/platform_profile") == "performance"
    assert rd("/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw") == "30000000"
    assert rd("/sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw") == "60000000"
    assert rd("/sys/class/powercap/intel-rapl-mmio:0/constraint_0_power_limit_uw") == "30000000"
    assert rd("/sys/devices/system/cpu/cpu1/cpufreq/energy_performance_preference") == "102"
    assert rd("/sys/devices/system/cpu/intel_pstate/no_turbo") == "1"
    assert rd("/sys/class/hwmon/hwmon3/fan2_boost") == "128"

    act.restore()
    assert rd("/sys/firmware/acpi/platform_profile") == "balanced"
    assert rd("/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw") == "45000000"
    assert rd("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference") == "balance_performance"
    assert rd("/sys/devices/system/cpu/intel_pstate/no_turbo") == "0"
    assert rd("/sys/class/hwmon/hwmon3/fan1_boost") == "0"


def test_linux_without_platform_profile_is_noop(tmp_path):
    r = DryRunner()
    act = LinuxActuator(r, sysfs_root=str(tmp_path))
    act.set_thermal_mode(ThermalMode.PERFORMANCE)
    assert r.actions == []
