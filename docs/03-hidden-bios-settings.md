# 3. Hidden BIOS settings via setup_var (Tier 1)

Dell ships many Intel reference options (power limits, C-states, ReBAR…) that
are hidden from the F2 menu but still read from NVRAM at boot. Writing those
NVRAM bytes changes the behavior **without modifying the signed BIOS image**,
so Boot Guard is not involved. Read `02-safety-and-recovery.md` first.

## What's worth looking for (x17 R2, hot climate)

| Option (typical Intel RC prompt) | Why | Tier |
|---|---|---|
| Power Limit 1 / 2, Power Limit 1 Time Window | Sustained/burst caps independent of AWCC. On Windows this replaces the frequency-cap workaround. | safe |
| Package C State Limit, C-States | Battery life / idle temps (deeper C-states = cooler idle) | safe |
| ASPM / L1 substates | Idle power of the dGPU/SSD links | safe |
| Re-Size BAR / Above 4G | AI and games: full 8 GB BAR for the GPU | safe |
| TCC Activation Offset | Make the CPU throttle earlier (e.g. at 90 °C) to protect a hot palm-rest/battery | safe |
| Intel DTT / DPTF options | Usually leave as is; listed for completeness | safe |
| Undervolt Protection, Overclocking Lock, CFG Lock | Would re-enable CPU undervolting **if** Dell doesn't enforce it elsewhere. Re-opens the Plundervolt (SGX) attack surface. | advanced |
| Any voltage, memory, BCLK, IccMax, security, flash or ME item | Can prevent POST or weaken security | **denied** |

Whether each item exists, and at which offset, depends on **your exact BIOS version**.

## Steps

1. **Extract** (offline, on any PC). Get BIOSUtilities, UEFIExtract and
   IFRExtractor-RS (links in the script header), then run:
   ```bash
   python tools/bios/extract_dell_bios.py Alienware_x17_R2_<ver>.exe -o extracted/<ver> \
       --biosutilities path/to/BIOSUtilities
   ```
   Or do the steps by hand in UEFITool: find the "Setup" DXE driver and run
   `ifrextractor <body.bin> verbose`. Then run `python tools/bios/ifr_to_json.py <file.txt> -o setup.json`.
2. **Find** options:
   ```bash
   python tools/bios/find_options.py extracted/<ver>/setup.json "power limit" "c state" "bar" "tcc"
   ```
   Note each option's QuestionId, VarStore name, offset and legal values.
3. **Generate** scripts (guarded by `allowlist.yaml` and the IFR's legal values):
   ```bash
   python tools/bios/gen_setup_var.py extracted/<ver>/setup.json --bios-version <ver> \
       --set "Re-Size BAR Support=Enabled" -o usb/
   ```
4. **USB stick**: FAT32, with `EFI/BOOT/BOOTX64.EFI` = a UEFI shell, plus
   `setup_var.efi` (https://github.com/datasone/setup_var.efi) and the generated
   `read.nsh`, `apply.nsh` and `revert.nsh`. Secure Boot must be temporarily **off**
   to boot an unsigned shell. Suspend BitLocker first.
5. Boot the stick (F12), run `read.nsh` and **photograph the output** (your
   original values). Then run `apply.nsh` and power off fully.
6. Boot Windows, turn Secure Boot back on in F2, and verify (HWiNFO, `nvidia-smi -q`, `x17tune probe`).
7. Something wrong? Boot the stick again and run `revert.nsh`, or write the
   photographed values back.

## Caveats
- Dell may re-apply its own values from another variable (e.g. a Dell-specific
  VarStore) or the EC may override power limits. If a change "doesn't stick",
  that is why; don't escalate to riskier options.
- A BIOS update can move offsets and may reset the variable. Regenerate the
  scripts for every BIOS version.
- If the NVRAM store is write-protected (setup_var reports an error or the value
  reverts), stop there.
