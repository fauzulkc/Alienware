# 8. Hot and humid climate field guide (Bangladesh, Thailand, Malaysia…)

At 30–40 °C ambient the laptop has 25–40 % less temperature headroom than in
the 22 °C rooms reviewers test in. The fans can't go faster than max. These
habits matter as much as any software.

## Airflow (biggest, cheapest wins)
- **Raise the rear 2–4 cm** (stand or even a book under the back). The x17 draws
  air from underneath. On a bed, sofa or blanket it suffocates. Never game there.
- Point the AC or room fan **at the intake side/underneath**, not at the screen.
  Cooler intake air is directly lower temperatures (that's the `T_room` term).
- A cooling pad helps only if its fans line up with the intakes. Measure with
  `bench/` before and after.
- Keep 10+ cm free behind the rear exhaust; don't push it against a wall.

## Dust + humidity = clogged fins
Humid air makes dust stick and cake on the fin stacks. That raises thermal
resistance week by week, and x17tune's `fouling` report is designed to catch it.
- Blow the intakes and exhausts out with compressed air **every 1–2 months**
  (hold the fans still with a toothpick so they don't over-spin).
- Open the bottom cover and clean the fin stacks **every 3–6 months** in dusty
  cities (Dhaka, Bangkok), or whenever `x17tune fouling` alerts. Then run
  `x17tune fouling --reset`.

## Thermal interface
- Dell advertises *Element 31* (gallium-based) on the x17 CPU. Liquid-metal-type
  TIMs don't pump out, so **don't repaste the CPU** unless temps prove it
  degraded. Gallium also attacks aluminium and is conductive, so this is an expert job.
- The GPU and VRAM use paste and pads. In heat, paste can pump out over 1–2 years.
  A **PTM7950 phase-change pad** on the GPU die is a popular durable fix.
  Re-use thermal pads of the **same thickness**. Repasting can affect warranty;
  check your Dell coverage first.

## Condensation
Moving the laptop from a cold AC room (or a car) into 30 °C+ humid air can form
condensation inside. **Let it sit 20–30 minutes in a closed bag before powering
on** after such a move. The reverse (hot humid → cold AC) is less risky but
still don't start a heavy load immediately.

## Battery
Heat is what kills lithium cells fastest; full charge plus heat is worst.
- If the laptop is mostly plugged in: BIOS (F2) → Power → **Primary Battery
  Charge Configuration → Custom, start 50 %, stop 80 %**, or use Dell Power
  Manager / Alienware Command Center if it exposes the same setting.
- Avoid gaming on battery (the battery profile limits power anyway).
- Watch for **battery swelling** (trackpad lifting, case gaps). Stop using it immediately.

## Power quality
Voltage dips and surges are common in some areas. Use a decent surge protector
or a UPS with AVR (sized for your 240–330 W adapter), and avoid running heavy AI jobs through
storms without one.

## What x17tune does for you here
- Estimates the room temperature and scales sustained power, temperature targets
  and the GPU undervolt slot (efficient slot from 33 °C up).
- Gives the shared heatsink to the component that matters for the workload.
- Forces max fan boost when 4 °C over target, before hardware throttling kicks in.
- Tracks heatsink health over weeks (`x17tune fouling`).
