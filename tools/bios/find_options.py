#!/usr/bin/env python3
"""Search the JSON from ifr_to_json.py.

    python find_options.py setup.json "power limit" "c state" "bar"
    python find_options.py setup.json --hidden-only "turbo"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def search(questions: list[dict], terms: list[str], hidden_only: bool = False) -> list[dict]:
    pats = [re.compile(t, re.I) for t in terms] or [re.compile(".")]
    out = []
    for q in questions:
        if hidden_only and not q.get("hidden"):
            continue
        hay = f"{q.get('prompt', '')} {q.get('help', '')}"
        if any(p.search(hay) for p in pats):
            out.append(q)
    return out


def fmt(q: dict) -> str:
    vals = (
        ", ".join(f"{o['name']}={o['value']}{'*' if o.get('default') else ''}" for o in q.get("options", []))
        or f"{q.get('min')}..{q.get('max')} (default {q.get('default')})"
    )
    flags = " ".join(f for f in (q.get("hidden") and f"hidden:{q['hidden']}", q.get("grayed") and f"grayed:{q['grayed']}") if f)
    off = q.get("offset")
    return (
        f"[0x{q.get('question_id') or 0:04X}] {q.get('prompt')!r:45} "
        f"{q.get('varstore_name')}@{'?' if off is None else hex(off)} size {q.get('size')}  {vals}  {flags}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json", type=Path)
    ap.add_argument("terms", nargs="*", help="regexes matched against prompt/help (case-insensitive)")
    ap.add_argument("--hidden-only", action="store_true")
    a = ap.parse_args(argv)
    qs = json.loads(a.json.read_text())
    for q in search(qs, a.terms, a.hidden_only):
        print(fmt(q))
    return 0


if __name__ == "__main__":
    sys.exit(main())
