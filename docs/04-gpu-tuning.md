# 4. GPU tuning (RTX 3070 Ti Laptop)

In a 30–40 °C room the fans are already at maximum, so the GPU's biggest gain
is **less voltage for the same clocks**. The CPU can't be undervolted on the
current BIOS; the GPU can.

## A. Find your efficiency knee (10 min, admin)
```powershell
py bench\gpu_curve_search.py sweep --points 1200 1400 1500 1600 1700 1800 1900 --seconds 45
```
This locks each clock ceiling, runs an fp16 matmul load and prints TFLOPS,
watts, TFLOPS/W and temperature. The *knee* is the highest clock within 5 % of
the best efficiency. Put it into `profiles.yaml → ai_inference.gpu_clock_max_mhz`.
LLM token generation is memory-bound, so tokens/s barely drops at the knee
while the GPU draws much less power.

## B. Build undervolt curves in MSI Afterburner (stock BIOS-safe)
Every chip differs, so these are starting points to test, not guaranteed values.

1. Afterburner → Ctrl+F (curve editor). Reset the curve.
2. Set the core clock offset so the point at your target voltage reaches the
   target clock (e.g. ~1800 MHz @ 0.850 V). Then Shift-drag to select every point
   to the right and drag them below that point, so the curve is flat.
3. Apply, then test stability:
   `py bench\gpu_curve_search.py verify --minutes 20`. Also run a demanding game
   for 30 minutes. Any error, artifact or driver reset → raise the voltage one step.
4. Save as slots:
   - **Slot 1 – efficient:** ~1600 MHz flat from ~0.775 V (AI, hot rooms)
   - **Slot 2 – performance:** ~1800 MHz flat from ~0.850 V (gaming)
   - **Slot 3 – stock** (reset curve): fallback
5. Afterburner → Settings → "Start with Windows" and "Apply overclocking at
   system startup" **off**. x17tune applies the slots.

x17tune switches slot 2 → slot 1 automatically when the room is "hot" or "extreme".

## C. VRAM
AI decode speed scales with memory bandwidth. A modest memory offset (+300 to
+700 MHz in Afterburner) can add a few % tok/s, but GDDR6 has error detection
and retry, so an *unstable* memory overclock shows up as **lower** speed, not
crashes. Benchmark with `bench/ai_bench.py llama` at each step. VRAM sits
close to the GPU heat pipes and gets hot in a 35 °C room, so be conservative there.

## D. Checks
- `nvidia-smi -q | findstr /C:"BAR1" /C:"Total"`: 8192 MiB means ReBAR is on.
- Keep the NVIDIA "Platform Controllers and Framework" driver installed (Dynamic Boost).
- Clock locks survive until reset. `x17tune restore` runs `nvidia-smi -rgc`.
