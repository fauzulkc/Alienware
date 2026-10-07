# 6. BIOS-mod research (Stage 2, gated, RISKY)

This stage answers one question: **can a modified BIOS image boot on this unit
at all?** It produces knowledge, not a flashable image. Expect the answer to be "no".

## Gate 0: is it worth it?
Everything a menu-unhide mod would give you can be reached with NVRAM writes
(`docs/03`) **without flashing**. Stage 2 only adds value for things that live
in code, not settings, and none of those are on this project's list.

## Gate 1: Boot Guard status (read-only)
- Linux: `sudo modprobe msr && sudo python3 tools/bios/bootguard_check.py`
- Windows: Intel CSME System Tools for ME 16 → `MEInfoWin64.exe -verbose > meinfo.txt`,
  then run `python tools/bios/bootguard_check.py --meinfo meinfo.txt`.

**Verified boot = enforced → STOP.** A modified initial boot block won't execute,
and even DXE-only edits are usually covered by Dell's own firmware-volume
verification. Document the result in `hardware-report/` and you're done.

## Gate 2 (only if Gate 1 says not enforcing, confirmed by MEInfo)
1. Hardware: CH341A programmer **with a 1.8 V adapter** (check the flash chip's
   voltage on its datasheet first), SOIC-8 clip, and the battery disconnected.
2. Dump the SPI flash **twice** and compare the hashes. Keep three copies
   (laptop, USB stick, cloud). Without a verified dump there is no way back.
3. Analyze it in UEFITool. Any edit would be limited to the Setup forms' IFR
   flags (unhide), the same thing AMIBCP-style tools do.
4. Only then consider whether to proceed, accepting that recovery means opening
   the laptop and re-writing the dump with the programmer.

This repo deliberately ships **no** image patcher.
