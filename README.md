# Alienware x17 R2 — performance and thermal tuning (i7-12700H + RTX 3070 Ti)

Get the most out of an Alienware x17 R2 for **AI workloads and gaming in hot,
humid rooms** (30–40 °C, South/Southeast Asia), with power that adjusts itself
to the workload and the room.

## Read this first: why this is not a custom BIOS

The original goal was "fork the BIOS and flash an optimized one". On this laptop
that is not safely possible, and nothing in this repo pretends otherwise:

| Obstacle | What it means |
|---|---|
| Dell-signed BIOS capsules (AMI Aptio core) | The official updater rejects any modified image. |
| Intel Boot Guard fused on Dell client boards | A modified image written with a hardware programmer is expected to **not boot**. |
| Dynamic power/fans live in the EC + Intel DTT + NVIDIA Dynamic Boost + AWCC | Even a modified BIOS would not hold the "auto-adjust" logic, and the EC firmware has no open replacement. |
| Undervolting locked since BIOS 1.9.1 (Plundervolt mitigation) | We stay on the current, patched BIOS (your choice). The GPU can still be undervolted. |

So the "smart BIOS" is built **where the platform allows it**:

```
            ┌──────────── x17tune (this repo, runs in Windows/Linux) ─────────────┐
 sensors →  │ classify workload → estimate room temp → climate band → PI loops    │ → levers
 (CPU/GPU   │ (AI / gaming / creator / quiet / battery)   (CPU PL1 ↔ GPU clock     │  AWCC thermal profile + fan boost
  temps,    │                                              share one heatsink)     │  CPU EPP / boost / freq cap / RAPL
  watts,    └──────────────────────────────────────────────────────────────────────┘  GPU clock lock + Afterburner undervolt slot
  procs)
```

## What's here

| Part | Path | Risk tier |
|---|---|---|
| **x17tune** auto-tuner: ambient-aware, workload-aware, dry-run by default | `tools/autotune/`, `install/` | Tier 0: OS settings only, `x17tune restore` undoes everything |
| GPU efficiency sweep and undervolt stability checker | `bench/gpu_curve_search.py` | Tier 0 |
| AI and game benchmarking with room-temperature logging | `bench/` | Tier 0 |
| Hidden-BIOS-option finder and guarded `setup_var` script generator | `tools/bios/` | **Tier 1**: writes NVRAM, see the warnings |
| Boot Guard check and BIOS-mod research gates | `tools/bios/bootguard_check.py`, `docs/06` | Tier 2: research only, no images produced |
| Hot and humid climate field guide | `docs/08-hot-humid-climate.md` | Hardware care |

## Quick start (Windows 11)

1. Install Python 3.11+ (python.org, "Add to PATH"), and
   [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
   set to start with Windows. It supplies CPU temperature and package power.
2. Optional: MSI Afterburner with GPU undervolt curves in profile slots 1–3
   (see `docs/04-gpu-tuning.md`).
3. Run these from an **elevated** PowerShell in the repo folder:
   ```powershell
   py -m pip install -e .[windows]
   py -m x17tune probe                    # read-only: which levers does your unit expose?
   py -m x17tune simulate --room 35       # see the control logic on a simulated x17
   powershell -ExecutionPolicy Bypass -File install\windows\install.ps1          # dry-run service
   # watch C:\ProgramData\x17tune\x17tune.log for a day, then:
   powershell -ExecutionPolicy Bypass -File install\windows\install.ps1 -Apply   # tune for real
   ```
4. Measure before and after: `docs/07-benchmarking.md`.

Undo everything: `install\windows\uninstall.ps1`, or `x17tune restore`.

Linux: `sudo ./install/linux/install.sh` (dry-run), or add `--apply`. Kernel 6.15+
recommended for the `alienware-wmi` thermal profiles and fan boost.

## Status: what is and isn't verified

- ✅ Control logic, ambient estimator, BIOS tooling: 71 automated tests, plus a
  closed-loop thermal simulator. At simulated rooms of 25, 32 and 38 °C it holds
  targets with no steady-state throttling.
- ⚠️ **Not yet run on real x17 R2 hardware.** The AWCC WMI codes come from the
  upstream Linux `alienware-wmi-wmax` driver. The Windows WMI argument name
  (`arg2`), the USTT profile table and the fan IDs must be confirmed with
  `x17tune probe` on your unit. The simulator's thermal constants are estimates.
  Run in dry-run first.
- Calibration data wanted: run `docs/local-hardware-report-prompt.md` on the laptop.

## Docs

1. [Hardware facts and limits](docs/01-hardware-facts.md)
2. [Safety and recovery](docs/02-safety-and-recovery.md) (read before anything Tier 1)
3. [Hidden BIOS settings via setup_var](docs/03-hidden-bios-settings.md)
4. [GPU tuning: undervolt, clock locks, efficiency for AI](docs/04-gpu-tuning.md)
5. [x17tune design and configuration](docs/05-autotune.md)
6. [BIOS-mod research (Stage 2, gated)](docs/06-bios-mod-research.md)
7. [Benchmarking method](docs/07-benchmarking.md)
8. [Hot and humid climate field guide](docs/08-hot-humid-climate.md)

## Development

```bash
pip install -e .[dev]
pytest -q
```
