#!/usr/bin/env python3
"""Generate UEFI-shell scripts that change hidden BIOS options via setup_var.efi.

Nothing here touches firmware. It writes three scripts you run yourself from a
FAT32 USB stick that has a UEFI shell + setup_var.efi
(https://github.com/datasone/setup_var.efi):

    read.nsh    print the current values (run first, photograph/save the output)
    apply.nsh   write the requested values
    revert.nsh  write the BIOS's stock defaults back

Every option is checked against allowlist.yaml (deny / advanced / safe) and
against the option's legal values from the IFR. Offsets come from YOUR BIOS
version's IFR - never reuse JSON from another version.

    python gen_setup_var.py setup.json --bios-version 1.28.0 \
        --set "Package C State Limit=C10" --set "0x2001=Enabled" -o usb/x17

Read docs/03-hidden-bios-settings.md before using the output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


class Refused(ValueError):
    pass


@dataclass
class Change:
    q: dict
    value: int
    tier: str  # safe / advanced / forced


def load_allowlist(path: Path | None = None) -> dict[str, list[re.Pattern]]:
    doc = yaml.safe_load((path or HERE / "allowlist.yaml").read_text())
    return {k: [re.compile(p, re.I) for p in doc.get(k, [])] for k in ("deny", "advanced", "safe")}


def classify(prompt: str, allow: dict[str, list[re.Pattern]]) -> str:
    for tier in ("deny", "advanced", "safe"):
        if any(p.search(prompt) for p in allow[tier]):
            return tier
    return "unknown"


def find_question(questions: list[dict], key: str) -> dict:
    key = key.strip()
    if re.fullmatch(r"0x[0-9a-fA-F]+|\d+", key):
        qid = int(key, 0)
        hits = [q for q in questions if q.get("question_id") == qid]
    else:
        hits = [q for q in questions if (q.get("prompt") or "").strip().lower() == key.lower()]
    if not hits:
        raise Refused(f"no option matches {key!r} (use find_options.py)")
    if len(hits) > 1:
        ids = ", ".join(f"0x{q.get('question_id') or 0:X}" for q in hits)
        raise Refused(f"{key!r} is ambiguous ({ids}); select it by QuestionId")
    return hits[0]


def parse_value(q: dict, raw: str) -> int:
    raw = raw.strip()
    opts = q.get("options") or []
    if opts:
        for o in opts:
            if o["name"].strip().lower() == raw.lower():
                return int(o["value"])
        try:
            v = int(raw, 0)
        except ValueError:
            raise Refused(f"{q['prompt']!r}: {raw!r} is not one of {[o['name'] for o in opts]}") from None
        if v not in {int(o["value"]) for o in opts}:
            raise Refused(f"{q['prompt']!r}: value {v} is not a legal option")
        return v
    try:
        v = int(raw, 0)
    except ValueError:
        raise Refused(f"{q['prompt']!r}: numeric value expected, got {raw!r}") from None
    lo, hi = q.get("min"), q.get("max")
    if lo is not None and v < lo or hi is not None and v > hi:
        raise Refused(f"{q['prompt']!r}: {v} outside [{lo}, {hi}]")
    step = q.get("step") or 0
    if step > 1 and lo is not None and (v - lo) % step:
        raise Refused(f"{q['prompt']!r}: {v} not a multiple of step {step}")
    return v


def plan(
    questions: list[dict],
    sets: list[str],
    allow: dict[str, list[re.Pattern]],
    allow_advanced: bool = False,
    force: bool = False,
) -> list[Change]:
    out: list[Change] = []
    seen: set[tuple] = set()
    for s in sets:
        if "=" not in s:
            raise Refused(f"--set expects NAME_OR_QID=VALUE, got {s!r}")
        key, raw = s.rsplit("=", 1)
        q = find_question(questions, key)
        tier = classify(q.get("prompt", ""), allow)
        if tier == "deny":
            raise Refused(f"{q['prompt']!r} is on the deny list (can brick or weaken security) - refusing")
        if tier == "advanced" and not allow_advanced:
            raise Refused(f"{q['prompt']!r} is an advanced option - pass --allow-advanced after reading the docs")
        if tier == "unknown":
            if not force:
                raise Refused(f"{q['prompt']!r} is not on the allowlist - pass --i-know-what-im-doing to override")
            tier = "forced"
        if q.get("type") not in ("OneOf", "Numeric", "CheckBox"):
            raise Refused(f"{q['prompt']!r}: only OneOf/Numeric/CheckBox options can be scripted")
        if None in (q.get("varstore_name"), q.get("offset"), q.get("size")):
            raise Refused(f"{q['prompt']!r}: VarStore/offset/size unknown in the IFR - refusing")
        loc = (q["varstore_name"], q["varstore_id"], q["offset"])
        if loc in seen:
            raise Refused(f"{q['prompt']!r}: the same variable offset is set twice")
        seen.add(loc)
        out.append(Change(q, parse_value(q, raw), tier))
    return out


def _cmd(q: dict, value: int | None) -> str:
    parts = ["setup_var.efi", hex(q["offset"])]
    if value is not None:
        parts.append(hex(value))
    parts += ["-s", hex(q["size"]), "-n", q["varstore_name"]]
    if q.get("varstore_id") is not None:
        parts += ["-i", hex(q["varstore_id"])]
    return " ".join(parts)


def render(changes: list[Change], bios_version: str) -> dict[str, str]:
    head = (
        f"# Generated by gen_setup_var.py for Alienware x17 R2 BIOS {bios_version}\n"
        "# DO NOT use with any other BIOS version - offsets differ between releases.\n"
        "# Syntax targets datasone/setup_var.efi; check `setup_var.efi -h` of your copy.\n"
    )
    read, apply, revert = [head + "echo -off\n"], [head + "echo -off\n"], [head + "echo -off\n"]
    for c in changes:
        q = c.q
        label = f"{q['prompt']} [{q['varstore_name']}@{hex(q['offset'])}, {q['size']} B, tier {c.tier}]"
        read += [f"echo \"{label}\"", _cmd(q, None)]
        apply += [f"echo \"set {label} -> {c.value}\"", _cmd(q, c.value)]
        if q.get("default") is None:
            revert += [f"echo \"{label}: stock default unknown - restore the value read.nsh printed\""]
        else:
            revert += [f"echo \"revert {label} -> {q['default']}\"", _cmd(q, int(q["default"]))]
    apply.append("echo \"Done. Power off fully (not reboot), then boot normally.\"")
    return {"read.nsh": "\n".join(read) + "\n", "apply.nsh": "\n".join(apply) + "\n", "revert.nsh": "\n".join(revert) + "\n"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json", type=Path, help="output of ifr_to_json.py for YOUR BIOS version")
    ap.add_argument("--bios-version", required=True, help="BIOS version the JSON came from (recorded in scripts)")
    ap.add_argument("--set", action="append", default=[], metavar="NAME_OR_QID=VALUE")
    ap.add_argument("-o", "--out", type=Path, required=True, help="output directory")
    ap.add_argument("--allowlist", type=Path)
    ap.add_argument("--allow-advanced", action="store_true", help="permit OC-lock / undervolt-protection toggles")
    ap.add_argument("--i-know-what-im-doing", dest="force", action="store_true", help="permit options not on the allowlist")
    a = ap.parse_args(argv)
    if not a.set:
        ap.error("nothing to do: pass at least one --set")
    try:
        changes = plan(json.loads(a.json.read_text()), a.set, load_allowlist(a.allowlist), a.allow_advanced, a.force)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    a.out.mkdir(parents=True, exist_ok=True)
    for name, text in render(changes, a.bios_version).items():
        (a.out / name).write_text(text, newline="\r\n")
    for c in changes:
        print(f"  {c.tier:8} {c.q['prompt']!r} -> {c.value}")
    print(f"wrote read.nsh / apply.nsh / revert.nsh to {a.out}. Run read.nsh FIRST and save its output.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
