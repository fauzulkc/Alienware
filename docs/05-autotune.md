# 5. x17tune: design and configuration

## Loop (1 Hz)
1. **Sense**: CPU temp and package power (LibreHardwareMonitor WMI on Windows;
   coretemp + RAPL on Linux), GPU temp, power, utilization, clock and throttle
   reasons (NVML), AC/battery, foreground app and running processes.
2. **Classify**: `rules.yaml` (process names, game install paths) first, then
   telemetry heuristics. The result is one of `ai_inference`, `ai_training`,
   `gaming`, `creator`, `balanced`, `quiet` or `battery`. A class must persist
   10 s before switching; battery switches immediately.
3. **Estimate the room**: recursive least squares fits
   `T = T_room + a·P_cpu + b·P_gpu` on quasi-steady samples. Until the
   estimate's uncertainty is ≤ 2 °C, the configured default (32 °C) is used. You
   can pin it with `--ambient 34`, or feed a smart thermometer's value through
   `--ambient-file`.
4. **Climate band** (`profiles.yaml → climate`): mild ≤28, warm ≤33, hot ≤38,
   extreme >38 °C, with 1 °C hysteresis. Each band scales sustained power,
   lowers temperature targets and (from hot upward) switches the GPU to its
   efficient undervolt slot and biases EPP toward efficiency.
5. **Control**: two PI loops under one heatsink.
   - CPU PL1 between the profile floor and the band-scaled ceiling. In GPU-bound
     classes the CPU also yields when the GPU is more than 2 °C over target.
   - GPU clock ceiling between the profile floor and ceiling (15 MHz bins, slow
     ramp-up, prompt step-down).
   - The integrators only pull *down*, so in a cool room the profile runs at
     full strength. A 1 °C deadband and hysteresis stop setting churn.
   - Emergency: more than 4 °C over target → fan boost 255 until 1 °C under target.
6. **Actuate** only what changed (see the back-ends below).

## Back-ends

| Lever | Windows | Linux |
|---|---|---|
| Thermal profile | AWCC WMI `Thermal_Control` | `platform_profile` |
| Fan boost | AWCC WMI | `alienware_wmi` hwmon `fanN_boost` |
| CPU sustained power | **frequency-cap inner loop** (PROCFREQMAX / PROCFREQMAX1) that holds measured package power at PL1. Windows has no driver-free PL1 control. | RAPL PL1/PL2 (MSR + MMIO) |
| CPU EPP / turbo | powercfg PERFEPP / PERFBOOSTMODE | intel_pstate EPP / no_turbo |
| GPU clock ceiling | `nvidia-smi -lgc` | same |
| GPU undervolt | Afterburner `-ProfileN` | optional `nvidia-settings` offsets |

If you unlock PL1/PL2 in the BIOS via `docs/03`, the AWCC thermal profile still
sets the EC's limits; x17tune's frequency cap only ever lowers them.

## Safety
- **Dry-run is the default.** It logs the exact commands it would run. Use `--apply` to act.
- On exit, error (5 in a row) or missing temperatures for 30 s, x17tune
  **restores stock behavior**: AWCC Balanced, fan boost 0, GPU clocks unlocked,
  the power-plan values snapshotted at start (or RAPL/EPP originals on Linux).
- Hard clamps in `profiles.yaml → limits` bound every profile.

## Configuration
Copy `tools/autotune/x17tune/config/profiles.yaml` / `rules.yaml` somewhere,
edit them, then run `x17tune run --profiles my.yaml --rules myrules.yaml`.
The values are starting points: tune `ai_inference.gpu_clock_max_mhz` from the
sweep in `docs/04`, and the CPU `cpu_pl1_w` values from your benchmarks.

## Heatsink health
Once a day x17tune records the fitted thermal resistances. After a
5-day baseline, if they rise by 10 % or more, `x17tune fouling` reports
"clean the fins". After cleaning or repasting, run `x17tune fouling --reset`.

## Commands
`probe`, `run [--apply]`, `simulate --room 36 --workload gaming`, `summarize file.csv`,
`fouling [--reset]`, `restore`.
