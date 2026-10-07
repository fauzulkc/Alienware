# Prompt: local hardware report for the x17 R2 tuning project

Paste everything below the line into a **local** Claude Code session running on
the Alienware x17 R2 itself, opened as Administrator: Windows Terminal → "Run as
administrator" → `claude`. The cloud session that builds `x17tune` will read the
result from the `hardware-report` branch.

---

You are on my Alienware x17 R2 (i7-12700H, RTX 3070 Ti Laptop, Windows 11). I'm
building an ambient-aware power/thermal auto-tuner in the GitHub repo
`fauzulkc/Alienware`, and a separate cloud session needs a detailed, accurate
picture of this machine. Produce that report.

## Hard rules (read-only audit)
- **Do not change anything.** No BIOS/UEFI variable writes, no power-plan
  changes, no fan/thermal-mode changes, no driver installs or updates, and no
  registry edits. Only query.
- For AWCC WMI, call **only** `Thermal_Information` / `GetFanSensors`-style
  *read* operations. **Never** call `Thermal_Control`, `GameShiftStatus` with
  toggle, or any method that sets something.
- Before installing any tool (HWiNFO64 portable is the only one I expect), ask me.
- Before running any load test, ask me and tell me how long it takes.
- **Redact** the Service Tag, serial numbers, GPU UUID/serial, MAC addresses,
  Windows username and product keys in everything you save (replace them with `<redacted>`).
- If a command fails or a tool is missing, record that and carry on. Don't guess values.

## Output
Create `hardware-report/` (in a clone of `https://github.com/fauzulkc/Alienware`,
branch `hardware-report` created from the default branch) containing:
- `report.md` — summary tables first, then sections below, each with the exact
  command used and its (redacted) output or a pointer to the raw file.
- `raw/` — full raw outputs (`.txt`), and the HWiNFO CSV logs.
At the end, commit and push the `hardware-report` branch (ask me first). If
pushing isn't possible, zip the folder and tell me where it is.

## What to collect

### 1. Identity and firmware
- Model/SKU, BIOS version and date, EC/firmware versions where visible:
  `Get-CimInstance Win32_ComputerSystem`, `Win32_BIOS`, `Win32_BaseBoard`,
  `Get-CimInstance -Namespace root\wmi MS_SystemInformation`.
- Windows edition/build (`winver` data via `Get-ComputerInfo`), Secure Boot
  (`Confirm-SecureBootUEFI`), **BitLocker status** (`manage-bde -status`, just
  protection on/off — important because BIOS changes can trigger recovery).
- Installed versions: NVIDIA driver, Alienware Command Center / Alienware
  Command Center services, Intel Dynamic Tuning Technology / Intel Innovation
  Platform Framework driver, Intel ME driver, Dell SupportAssist / Dell Power
  Manager, MSI Afterburner, ThrottleStop, Intel XTU (installed or not).

### 2. CPU
- `Get-CimInstance Win32_Processor | fl *` (name, cores, threads, max clock).
- Current power plan and all processor settings incl. hidden ones:
  `powercfg /list`, `powercfg /qh SCHEME_CURRENT SUB_PROCESSOR`, `powercfg /a`.
- From HWiNFO (section 6): PL1, PL2, Tau, IccMax, whether "Undervolt Protection"
  or OC lock is reported, and core voltage behavior.

### 3. GPU
- `nvidia-smi -q` (redact UUID/serial) and
  `nvidia-smi -q -d POWER,CLOCK,SUPPORTED_CLOCKS,PERFORMANCE,TEMPERATURE`.
- vBIOS version, default/enforced/min/max power limit, BAR1 size (8 GiB means
  Resizable BAR is on), Dynamic Boost presence (`nvidia-smi -q` / NVIDIA
  Control Panel → System Information), MUX / Advanced Optimus state
  (AWCC or NVIDIA Control Panel "Display mode").
- Test **without changing anything** whether clock locking is permitted:
  `nvidia-smi -lgc` with no arguments only prints usage, so instead report the
  "Clocks Event Reasons"/"Applications Clocks" sections and whether nvidia-smi
  reports "Not Supported" for application clocks.

### 4. AWCC WMI (read-only)
- `Get-CimClass -Namespace root\WMI -ClassName AWCCWmiMethodFunction` → list all
  methods with their input/output parameter names and types.
- If a `Thermal_Information` method exists, call it read-only with the packed
  32-bit argument `operation | (arg1 << 8)` for:
  - operation `0x02` (system description → fan count, sensor count, profile count),
  - operation `0x03` with arg1 = 0..(fan+sensor+unknown+profile count − 1)
    (resource IDs: fan IDs, sensor IDs, thermal profile IDs),
  - operation `0x0B` (current thermal profile),
  - operation `0x05` with each fan ID (fan RPM), `0x08`/`0x09` (min/max RPM),
  - operation `0x04` with each sensor ID (temperature),
  - operation `0x0C` with each fan ID (current fan boost).
  Decode the profile IDs with this table: 0x96 legacy quiet, 0x97 legacy
  balanced, 0x98 legacy balanced-performance, 0x99 legacy performance, 0xA0
  USTT balanced, 0xA1 USTT balanced-performance, 0xA2 USTT cool, 0xA3 USTT quiet,
  0xA4 USTT performance, 0xA5 USTT low-power, 0xAB G-Mode.
  Print the raw return values in hex too.

### 5. Storage, memory, battery
- RAM size/speed/slots (`Win32_PhysicalMemory`), SSD models and temperatures if
  visible (`Get-PhysicalDisk`, `Get-StorageReliabilityCounter`).
- `powercfg /batteryreport /output raw\battery.html` → design vs full charge
  capacity and cycle count; and whether Dell battery charge settings are
  visible (Dell Power Manager / BIOS "Primary Battery Charge Configuration").

### 6. Thermal logs (ask me before this; ~30 min total)
Use HWiNFO64 portable (sensors-only, logging to CSV every 2 s). Before starting,
ask me for: **room temperature**, whether AC is running, what the laptop sits on
(desk / stand / cooling pad), and the current AWCC thermal mode.
1. `idle.csv` — 5 min idle, plugged in.
2. `game.csv` — 10 min of a game I choose (ask me which).
3. `ai.csv` — 10 min of an AI workload: if Ollama exists, `ollama run` a 7–8B model
   in a loop with a long prompt; otherwise `llama-bench` or a PyTorch matmul loop
   (ask me which is available).
For each log, summarize in `report.md`: avg/max CPU package temp, CPU package
power, P-core/E-core clocks, GPU temp, GPU hotspot, GPU memory junction (if shown),
GPU power, GPU clock, fan RPMs, and any thermal/power-limit throttling flags
(CPU "Thermal Throttling", "Power Limit Exceeded"; GPU "Performance Limit").

### 7. BIOS setup (manual, ask me)
Ask me to reboot into BIOS (F2) and either photograph or list every page and
option under Performance / Advanced / Power / Thermal (I'll paste them back),
including whether there's an "Advanced Overclocking", "Undervolting",
"CFG Lock" or "OC Lock" item and its value. Record what I report.

### 8. Summary
At the top of `report.md` give a table: BIOS version, NVIDIA driver, vBIOS, GPU
power limit (default/max), ReBAR on/off, MUX mode, AWCC profile IDs available,
fan IDs, PL1/PL2/Tau, undervolt status, BitLocker on/off, Secure Boot on/off,
room temp during logs, and the worst-case CPU/GPU temps and throttle flags seen.
