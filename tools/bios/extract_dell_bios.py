#!/usr/bin/env python3
"""Extract the Setup IFR from an *official* Dell BIOS update file - offline, no flashing.

Pipeline (each tool is third-party; this script only orchestrates and prints
what it runs so you can do the same by hand):

  1. Dell PFS extract  - platomav/BIOSUtilities  (Dell_PFS_Extract / `dell_pfs_extract`)
     Alienware-x17-R2-<ver>.exe  ->  PFS sections incl. the 'System BIOS' firmware volume image
  2. UEFIExtract       - LongSoft/UEFITool (NE)  : unpack the BIOS image into a folder tree
  3. find the Setup driver body (a PE32 section that contains IFR "FormSet"s; usually
     the DXE driver named "Setup" - we pick bodies containing the 'Setup' UTF-16 string)
  4. IFRExtractor-RS   - LongSoft/IFRExtractor-RS : PE32 body -> human-readable IFR .txt
  5. ifr_to_json.py    - this repo

Example:
  python extract_dell_bios.py Alienware_x17_R2_1.28.0.exe -o extracted/1.28.0 \
      --biosutilities ~/src/BIOSUtilities --uefiextract UEFIExtract --ifrextractor ifrextractor
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETUP_MARKER = "Setup".encode("utf-16-le")


def run(argv: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(str(x) for x in argv), flush=True)
    subprocess.run([str(x) for x in argv], cwd=cwd, check=True)


def find_setup_bodies(root: Path) -> list[Path]:
    """PE32 bodies that look like they hold the Setup forms (largest first)."""
    cands = []
    for p in root.rglob("body.bin"):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        # 0x0E 0xA7 = IFR FormSet opcode with length 0x27 + scope bit (one class GUID).
        if data[:2] == b"MZ" and SETUP_MARKER in data and b"\x0e\xa7" in data:
            cands.append(p)
    return sorted(cands, key=lambda p: p.stat().st_size, reverse=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dell_exe", type=Path, help="official Dell BIOS update .exe")
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--biosutilities", type=Path, help="path to a BIOSUtilities checkout")
    ap.add_argument("--uefiextract", default="UEFIExtract")
    ap.add_argument("--ifrextractor", default="ifrextractor")
    a = ap.parse_args(argv)

    a.out.mkdir(parents=True, exist_ok=True)
    pfs_out = a.out / "pfs"
    if a.biosutilities:
        main_py = a.biosutilities / "main.py"
        if main_py.exists():
            run([sys.executable, main_py, "-e", "-o", pfs_out, a.dell_exe])
        else:
            run([sys.executable, "-m", "biosutilities.dell_pfs_extract", "-o", pfs_out, a.dell_exe], cwd=a.biosutilities)
    else:
        print("Step 1 needs BIOSUtilities: git clone https://github.com/platomav/BIOSUtilities and pass --biosutilities")
        return 2

    images = sorted(pfs_out.rglob("*System BIOS*.bin"), key=lambda p: p.stat().st_size, reverse=True) or sorted(
        pfs_out.rglob("*.bin"), key=lambda p: p.stat().st_size, reverse=True
    )
    if not images:
        print(f"no BIOS image found under {pfs_out}")
        return 2
    bios = images[0]
    print(f"BIOS image: {bios}")
    if shutil.which(a.uefiextract) is None:
        print(f"{a.uefiextract} not found - get UEFIExtract from https://github.com/LongSoft/UEFITool/releases")
        return 2
    run([a.uefiextract, bios, "all"])
    dump = Path(str(bios) + ".dump")
    bodies = find_setup_bodies(dump)
    if not bodies:
        print("could not locate the Setup driver automatically; open the image in UEFITool and search for 'Setup'")
        return 2
    if shutil.which(a.ifrextractor) is None:
        print(f"{a.ifrextractor} not found - get it from https://github.com/LongSoft/IFRExtractor-RS/releases")
        return 2
    for b in bodies[:3]:
        run([a.ifrextractor, b, "verbose"])
    txts = sorted(dump.rglob("*.ifr.txt"), key=lambda p: p.stat().st_size, reverse=True)
    if not txts:
        print("IFRExtractor produced no .txt output")
        return 2
    run([sys.executable, HERE / "ifr_to_json.py", txts[0], "-o", a.out / "setup.json"])
    print(f"\nDone: {a.out / 'setup.json'}  (IFR text: {txts[0]})")
    print("Next: python tools/bios/find_options.py", a.out / "setup.json", '"power limit" "c state" "bar"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
