# 7. Benchmarking method

Results from a 28 °C morning and a 36 °C afternoon can't be compared. **Always
record the room temperature** (a cheap digital thermometer near the intake), and
compare runs within ±1.5 °C of each other.

## Protocol
1. Same power state: plugged in, same AWCC mode, lid open, same surface/stand.
2. Let the laptop idle 5 min to settle.
3. **Baseline** (x17tune stopped, `x17tune restore`):
   ```powershell
   py bench\ai_bench.py matmul --seconds 120 --room 33 --label stock
   py bench\ai_bench.py llama --model C:\models\llama-3.1-8b-q4_k_m.gguf --room 33 --label stock
   ```
   Then a game benchmark (built-in benchmark or a fixed 10-minute route) with
   HWiNFO logging, or `x17tune run --log stock_game.csv` in dry-run.
4. **Tuned** (x17tune `--apply` running): the same commands with `--label x17tune`
   and `--log tuned_game.csv`.
5. Compare:
   ```powershell
   py bench\compare.py results
   py bench\compare.py csv stock_game.csv tuned_game.csv
   ```

## What "better" means here
- **AI:** tok/s per watt and sustained tok/s after 10+ minutes (not the first 30 s).
- **Gaming:** average and 1 % low FPS after heat-soak, with throttle events per hour (`gpu_throttling_events_per_h`).
- **Comfort:** peak CPU/GPU temps, fan RPM, palm-rest feel.

A typical good outcome in a hot room: similar or slightly lower peak FPS,
**higher sustained FPS and 1 % lows**, 15–30 % less GPU power for AI at a few %
lower tok/s, and zero thermal-throttle events.
