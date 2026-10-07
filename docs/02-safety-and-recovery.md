# 2. Safety and recovery

## Risk tiers

| Tier | What | Undo |
|---|---|---|
| 0 | x17tune, Afterburner, power plans, `nvidia-smi` clock locks | `x17tune restore` / uninstall / reboot (clock locks and AWCC profile persist until reset, so use restore) |
| 1 | Writing BIOS **NVRAM** Setup variables (`setup_var.efi`) | `revert.nsh`, or the values you saved from `read.nsh`. **An RTC reset is not guaranteed to clear NVRAM on Dell laptops.** |
| 2 | Flashing a modified BIOS with an SPI programmer | Only by writing your original dump back with the programmer. Not done in this repo. |

## Before any Tier 1 change
1. **Suspend BitLocker.** BIOS changes alter TPM measurements and can trigger a
   48-digit recovery-key prompt.
   `manage-bde -protectors -disable C: -RebootCount 1`. Also have your recovery
   key ready (https://account.microsoft.com/devices/recoverykey).
2. Back up anything important. Record the BIOS version (`x17tune probe`).
3. Make a Dell BIOS recovery USB (below) **before** you need it.
4. Change **one** option per boot, and only from the allowlist's *safe* tier.

## Dell BIOS recovery USB (have it ready)
Dell laptops can re-flash the BIOS from USB or from a file on the drive when the
BIOS is corrupted. The usual procedure (confirm against Dell's KB article for
"BIOS recovery" on your model):
1. Download the **same or newer** official x17 R2 BIOS `.exe` from Dell.
2. Rename it to `BIOS_IMG.rcv` and copy it to the root of a FAT32 USB stick.
3. Laptop off, AC adapter connected, USB inserted. Hold **Ctrl + Esc**, press
   power, and release when the BIOS Recovery screen appears. Choose recovery
   from USB.

BIOS recovery re-flashes the firmware; it may or may not reset NVRAM Setup
variables. If a bad variable survives, use "Restore BIOS defaults" in F2 setup.
Failing that, run `revert.nsh` from the UEFI shell.

## RTC / CMOS reset (Dell laptops)
With the laptop off and the AC adapter connected, hold the power button for
about 25–35 s until the power LED flashes and the system reboots. This resets
the clock and some BIOS settings. **Do not count on it to undo NVRAM edits.**

## Never do (in this project)
- Write voltage offsets, memory timings/frequency/voltage, BCLK, IccMax or load-line
  values through NVRAM. These can stop the machine from POSTing. The deny list
  in `tools/bios/allowlist.yaml` enforces this.
- Touch anything named Boot Guard, BIOS Guard, BIOS Lock, SMM, flash
  protection, ME, TPM/PTT or Secure Boot in NVRAM.
- Flash any BIOS that isn't an official Dell file through Dell's own updater.
- Cross-flash the GPU vBIOS from another laptop model.
- Run AI training or benchmarks overnight in a 38 °C room without the
  emergency fan boost enabled (x17tune default) and without checking logs.
