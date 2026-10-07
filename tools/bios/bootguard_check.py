#!/usr/bin/env python3
"""Read-only Intel Boot Guard status check.

Linux (root):  reads MSR 0x13A (BOOT_GUARD_SACM_INFO) through /dev/cpu/0/msr
               (`sudo modprobe msr` first).
Windows:       there is no driver-free MSR access; run Intel CSME System Tools
               `MEInfoWin64.exe -verbose` and pass its output with --meinfo, or
               use chipsec (`chipsec_main -m common.bios_wp` etc.).

Bit layout of MSR 0x13A as used by chipsec / coreboot documentation:
  bit 0      NEM (no-eviction mode) enabled by the ACM
  bits 1-2   TPM type, bit 3 TPM success
  bit 4      force anchor boot
  bit 5      measured boot
  bit 6      verified boot
  bit 7      ACM module revoked
  bit 32     Boot Guard capability
If "verified boot" is set, modified firmware in the initial boot block will not
run - see docs/06-bios-mod-research.md.
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import sys
from pathlib import Path

MSR_BOOT_GUARD_SACM_INFO = 0x13A


def decode(v: int) -> dict:
    return {
        "raw": f"0x{v:016X}",
        "nem_enabled": bool(v & 1),
        "tpm_type": (v >> 1) & 0b11,
        "tpm_success": bool(v >> 3 & 1),
        "force_anchor_boot": bool(v >> 4 & 1),
        "measured_boot": bool(v >> 5 & 1),
        "verified_boot": bool(v >> 6 & 1),
        "acm_revoked": bool(v >> 7 & 1),
        "boot_guard_capable": bool(v >> 32 & 1),
    }


def verdict(d: dict) -> str:
    if d["verified_boot"]:
        return "ENFORCED: Boot Guard verified boot is active - a modified BIOS image will not boot (Stage 2 = stop)."
    if d["measured_boot"]:
        return "MEASURED ONLY: firmware is measured into the TPM but not verified. Still treat flashing as high risk."
    if d["boot_guard_capable"]:
        return "CAPABLE BUT NOT ACTIVE per this MSR. Confirm with MEInfo before believing it."
    return "No Boot Guard reported by this MSR. Confirm with MEInfo - MSR results alone are not conclusive."


def read_msr_linux(reg: int, cpu: int = 0) -> int:
    path = f"/dev/cpu/{cpu}/msr"
    fd = os.open(path, os.O_RDONLY)
    try:
        os.lseek(fd, reg, os.SEEK_SET)
        return struct.unpack("<Q", os.read(fd, 8))[0]
    finally:
        os.close(fd)


def parse_meinfo(text: str) -> dict:
    """Pull the Boot Guard lines out of MEInfo -verbose output."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*(.*?(Boot Guard|Verified Boot|Measured Boot|Protect BIOS|ACM|Key Manifest|FPF).*?)\s{2,}(.+)$", line, re.I)
        if m:
            out[m.group(1).strip()] = m.group(3).strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meinfo", type=Path, help="text file with `MEInfoWin64 -verbose` output")
    ap.add_argument("--value", help="decode this MSR 0x13A value instead of reading it")
    a = ap.parse_args(argv)

    if a.meinfo:
        rows = parse_meinfo(a.meinfo.read_text(errors="replace"))
        if not rows:
            print("no Boot Guard lines found in MEInfo output")
        for k, v in rows.items():
            print(f"{k:50} {v}")
        return 0

    if a.value:
        v = int(a.value, 0)
    else:
        if not sys.platform.startswith("linux"):
            print("MSR reads need Linux (or use --meinfo on Windows).", file=sys.stderr)
            return 2
        try:
            v = read_msr_linux(MSR_BOOT_GUARD_SACM_INFO)
        except OSError as e:
            print(f"cannot read MSR ({e}). Try: sudo modprobe msr && sudo {sys.argv[0]}", file=sys.stderr)
            return 2
    d = decode(v)
    for k, val in d.items():
        print(f"{k:20} {val}")
    print(verdict(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
