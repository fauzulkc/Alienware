# 1. Hardware facts and limits

Values marked *(check)* vary by SKU or BIOS. Confirm them with `x17tune probe`
and the hardware report (`docs/local-hardware-report-prompt.md`).

## CPU: Intel Core i7-12700H (Alder Lake-H)
- 6 P-cores + 8 E-cores, 20 threads; P-core max turbo 4.7 GHz, E-core 3.5 GHz.
- Intel spec: 45 W base power, 115 W maximum turbo power; Tjmax 100 °C.
- Dell sets the real PL1/PL2/Tau per AWCC thermal profile *(check: HWiNFO →
  "CPU Package Power Limits")*.
- **Undervolting:** blocked on current BIOS (Intel "undervolt protection" since
  the Plundervolt fixes; Dell BIOS 1.9.1+). Whether the hidden "Undervolt
  Protection" / OC-lock options can be flipped via NVRAM on this BIOS is
  unverified (`docs/03`, advanced tier).
- Efficiency: sustained throughput grows much more slowly than power past
  about 45 W. That is why x17tune caps the CPU near its efficient point in hot
  rooms and gives the heatsink capacity to the GPU.

## GPU: RTX 3070 Ti Laptop (GA104, 8 GB GDDR6)
- Dell's configured TGP including Dynamic Boost *(check `nvidia-smi -q -d POWER`;
  reviews of the x17 R2 report roughly 140–150 W)*. The vBIOS is NVIDIA-signed,
  so TGP cannot be raised by editing it, and cross-flashing is out of scope here.
- **Undervolting works** through the V/F curve (MSI Afterburner), and clock
  ceilings can be locked with `nvidia-smi -lgc`. Ampere power is roughly
  proportional to f·V², so a lower clock and voltage give a large drop in watts
  for a small loss in speed.
- Resizable BAR: check that BAR1 total = 8192 MiB in `nvidia-smi -q`.
- Display mode / MUX: hybrid vs discrete-only *(check AWCC / BIOS)*. Discrete
  mode helps games; hybrid saves battery.

## Cooling and power topology
- CPU and GPU share heat pipes and fin stacks, so heat from one raises the other.
  x17tune models this: the CPU loop yields to the GPU in GPU-bound workloads.
- The EC (embedded controller) runs the fan curves. AWCC selects EC thermal
  tables ("USTT" profiles: Balanced, Balanced-Performance, Cool, Quiet,
  Performance, Low-Power) and can add a per-fan *boost* (0–255).
- Intel DTT (Dynamic Tuning) and NVIDIA Dynamic Boost shift power between the
  CPU and GPU automatically. Keep the "Intel Innovation Platform Framework" and
  "NVIDIA Platform Controllers and Framework" drivers installed; x17tune works
  on top of them, not instead of them.
- Dell advertises "Element 31" (gallium-silicone) TIM on the x17. Find out what
  your unit has before opening it (`docs/08`).

## AWCC WMI interface (what x17tune drives)
Source: upstream Linux driver `drivers/platform/x86/dell/alienware-wmi-wmax.c`.

| Method | Operation | Meaning |
|---|---|---|
| `Thermal_Information` (0x14) | 0x02 | system description: fan, sensor and profile counts |
| | 0x03 + index | resource ID (fan IDs, sensor IDs, profile IDs) |
| | 0x05 + fan | fan RPM; 0x08/0x09 min/max RPM |
| | 0x0B | current profile |
| | 0x0C + fan | current fan boost |
| `Thermal_Control` (0x15) | 0x01 + profile | activate profile |
| | 0x02 + fan + boost | set fan boost |

Arguments are packed `op | arg1<<8 | arg2<<16 | arg3<<24`. Profile codes:
USTT 0xA0 balanced, 0xA1 balanced-performance, 0xA2 cool, 0xA3 quiet,
0xA4 performance, 0xA5 low-power; legacy 0x96–0x99; G-Mode 0xAB (the Linux
driver does not enable G-Mode on the x17).
