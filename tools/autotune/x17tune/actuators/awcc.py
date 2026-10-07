"""Alienware Command Center (AWCC) WMI constants.

Taken from the upstream Linux driver drivers/platform/x86/dell/alienware-wmi-wmax.c,
which drives the same ACPI WMI interface (WMI class `AWCCWmiMethodFunction`
in `root\\WMI` on Windows). Arguments are packed little-endian into one u32:

    operation | arg1 << 8 | arg2 << 16 | arg3 << 24
"""

from __future__ import annotations

from ..model import ThermalMode

METHOD_GET_FAN_SENSORS = 0x13
METHOD_THERMAL_INFORMATION = 0x14
METHOD_THERMAL_CONTROL = 0x15

# Thermal_Information operations (read-only)
OP_GET_SYSTEM_DESCRIPTION = 0x02
OP_GET_RESOURCE_ID = 0x03
OP_GET_TEMPERATURE = 0x04
OP_GET_FAN_RPM = 0x05
OP_GET_FAN_MIN_RPM = 0x08
OP_GET_FAN_MAX_RPM = 0x09
OP_GET_CURRENT_PROFILE = 0x0B
OP_GET_FAN_BOOST = 0x0C

# Thermal_Control operations (write)
OP_ACTIVATE_PROFILE = 0x01
OP_SET_FAN_BOOST = 0x02

# Fan IDs (the x17 R2 typically reports CPU_1 and GPU_1; confirm with `x17tune probe`)
FAN_CPU_1 = 0x32
FAN_GPU_1 = 0x33

LEGACY = {
    ThermalMode.QUIET: 0x96,
    ThermalMode.BALANCED: 0x97,
    ThermalMode.BALANCED_PERFORMANCE: 0x98,
    ThermalMode.PERFORMANCE: 0x99,
}
USTT = {
    ThermalMode.BALANCED: 0xA0,
    ThermalMode.BALANCED_PERFORMANCE: 0xA1,
    ThermalMode.COOL: 0xA2,
    ThermalMode.QUIET: 0xA3,
    ThermalMode.PERFORMANCE: 0xA4,
    ThermalMode.LOW_POWER: 0xA5,
}
GMODE = 0xAB  # not used on the x17 (the Linux driver leaves G-Mode off for this model)

# Fallback order when a table lacks a mode.
FALLBACK = {
    ThermalMode.LOW_POWER: [ThermalMode.QUIET, ThermalMode.BALANCED],
    ThermalMode.COOL: [ThermalMode.QUIET, ThermalMode.BALANCED],
    ThermalMode.QUIET: [ThermalMode.BALANCED],
    ThermalMode.BALANCED_PERFORMANCE: [ThermalMode.PERFORMANCE, ThermalMode.BALANCED],
    ThermalMode.PERFORMANCE: [ThermalMode.BALANCED_PERFORMANCE, ThermalMode.BALANCED],
    ThermalMode.BALANCED: [],
}

PROFILE_NAMES = {v: f"legacy_{k.value}" for k, v in LEGACY.items()} | {v: f"ustt_{k.value}" for k, v in USTT.items()}
PROFILE_NAMES[GMODE] = "gmode"


def pack(operation: int, arg1: int = 0, arg2: int = 0, arg3: int = 0) -> int:
    for v in (operation, arg1, arg2, arg3):
        if not 0 <= v <= 0xFF:
            raise ValueError("AWCC args are bytes")
    return operation | arg1 << 8 | arg2 << 16 | arg3 << 24


def profile_code(mode: ThermalMode, table: str = "ustt") -> int:
    t = USTT if table == "ustt" else LEGACY
    for m in [mode, *FALLBACK[mode]]:
        if m in t:
            return t[m]
    return t[ThermalMode.BALANCED]
