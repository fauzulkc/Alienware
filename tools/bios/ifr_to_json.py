#!/usr/bin/env python3
"""Parse IFRExtractor-RS text output into JSON you can search and script against.

Input:  the `.txt` that IFRExtractor-RS (https://github.com/LongSoft/IFRExtractor-RS)
        writes for the Setup module of *your* BIOS version, e.g.
            ifrextractor Setup.sct verbose      ->  Setup.sct.0.0.en-US.uefi.ifr.txt
Output: a JSON list of questions with prompt, VarStore (name + GUID), offset,
        size in bytes, options/min/max, the stock default and whether the item is
        hidden (SuppressIf) or greyed out (GrayOutIf) in the stock menu.

Read-only and offline: it never touches firmware.

    python ifr_to_json.py Setup.ifr.txt -o setup.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_KV = re.compile(r'(\w+):\s*("(?:[^"\\]|\\.)*"|[^,\s]+)')

QUESTION_OPS = {"OneOf", "Numeric", "CheckBox", "String", "Password", "OrderedList"}
SCOPE_OPS_HIDE = {"SuppressIf": "suppressed", "GrayOutIf": "grayed", "DisableIf": "suppressed"}


@dataclass
class Option:
    name: str
    value: int
    default: bool = False


@dataclass
class Question:
    type: str
    prompt: str
    help: str
    question_id: int | None
    varstore_id: int | None
    varstore_name: str | None
    varstore_guid: str | None
    offset: int | None
    size: int | None  # bytes
    min: int | None = None
    max: int | None = None
    step: int | None = None
    options: list[Option] = field(default_factory=list)
    default: int | None = None
    hidden: str | None = None  # "always", "conditional" or None
    grayed: str | None = None
    formset: str | None = None
    form: str | None = None


def _num(v: str | None) -> int | None:
    if v is None:
        return None
    v = v.strip()
    try:
        return int(v, 16) if v.lower().startswith("0x") else int(v)
    except ValueError:
        return None


def _kv(line: str) -> dict[str, str]:
    out = {}
    for k, v in _KV.findall(line):
        v = v.strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        out[k] = v
    return out


def _indent(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip(" "))


def _size_bytes(op: str, raw: str | None) -> int | None:
    if op == "CheckBox":
        return 1
    n = _num(raw)
    if n is None:
        return None
    # IFRExtractor-RS prints OneOf/Numeric widths in bits (8/16/32/64).
    return n // 8 if n in (8, 16, 32, 64) else n


def parse(text: str) -> list[Question]:
    varstores: dict[int, tuple[str, str]] = {}
    questions: list[Question] = []
    # stack entries: (indent, kind, extra)
    stack: list[tuple[int, str, dict]] = []
    formset = form = None
    current: Question | None = None
    current_indent = -1

    lines = text.splitlines()
    for i, raw in enumerate(lines):
        if not raw.strip():
            continue
        ind = _indent(raw)
        line = raw.strip()
        op = line.split(" ", 1)[0]

        while stack and stack[-1][0] >= ind:
            stack.pop()
        if current is not None and ind <= current_indent:
            current = None

        if op == "FormSet":
            formset = _kv(line).get("Title")
        elif op == "VarStore" or op == "VarStoreEfi":
            kv = _kv(line)
            vid = _num(kv.get("VarStoreId"))
            if vid is not None:
                varstores[vid] = (kv.get("Name", ""), kv.get("Guid", ""))
        elif op == "Form":
            form = _kv(line).get("Title")
        elif op in SCOPE_OPS_HIDE:
            # Peek at the first child: a literal "True" means unconditional.
            always = False
            for nxt in lines[i + 1 :]:
                if nxt.strip():
                    always = _indent(nxt) > ind and nxt.strip().split(" ", 1)[0] == "True"
                    break
            stack.append((ind, SCOPE_OPS_HIDE[op], {"always": always}))
            continue
        elif op in QUESTION_OPS:
            kv = _kv(line)
            vid = _num(kv.get("VarStoreId"))
            vname, vguid = varstores.get(vid, (None, None)) if vid is not None else (None, None)
            hidden = grayed = None
            for _, kind, extra in stack:
                level = "always" if extra.get("always") else "conditional"
                if kind == "suppressed":
                    hidden = "always" if (hidden == "always" or level == "always") else level
                elif kind == "grayed":
                    grayed = "always" if (grayed == "always" or level == "always") else level
            q = Question(
                type=op,
                prompt=kv.get("Prompt", ""),
                help=kv.get("Help", ""),
                question_id=_num(kv.get("QuestionId")),
                varstore_id=vid,
                varstore_name=vname,
                varstore_guid=vguid,
                offset=_num(kv.get("VarOffset")),
                size=_size_bytes(op, kv.get("Size")),
                min=_num(kv.get("Min")),
                max=_num(kv.get("Max")),
                step=_num(kv.get("Step")),
                hidden=hidden,
                grayed=grayed,
                formset=formset,
                form=form,
            )
            if op == "CheckBox":
                q.min, q.max = 0, 1
                d = kv.get("Default")
                if d is not None:
                    q.default = 1 if d.lower() in ("enabled", "1", "true") else 0
            questions.append(q)
            current, current_indent = q, ind
        elif op == "OneOfOption" and current is not None:
            m = re.match(r'OneOfOption Option:\s*"((?:[^"\\]|\\.)*)"\s*Value:\s*([0-9a-fA-Fx]+)(.*)', line)
            if m:
                # Flags look like ", Default, MfgDefault"; only the standard "Default" counts.
                is_default = bool(re.search(r"(^|[ ,])Default($|[ ,])", m.group(3)))
                opt = Option(m.group(1), _num(m.group(2)) or 0, is_default)
                current.options.append(opt)
                if opt.default and current.default is None:
                    current.default = opt.value
        elif op == "Default" and current is not None:
            kv = _kv(line)
            if current.default is None and _num(kv.get("DefaultId")) in (0, None):
                current.default = _num(kv.get("Value"))
        if op in ("Form", "FormSet") or op in QUESTION_OPS:
            stack.append((ind, op, {}))
    return questions


def to_jsonable(qs: list[Question]) -> list[dict]:
    return [asdict(q) for q in qs]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ifr_txt", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="write JSON here (default: stdout)")
    a = ap.parse_args(argv)
    qs = parse(a.ifr_txt.read_text(encoding="utf-8", errors="replace"))
    data = json.dumps(to_jsonable(qs), indent=1)
    if a.out:
        a.out.write_text(data)
        hidden = sum(1 for q in qs if q.hidden)
        print(f"{len(qs)} questions ({hidden} hidden in the stock menu) -> {a.out}", file=sys.stderr)
    else:
        print(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
