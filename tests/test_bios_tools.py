import json
from pathlib import Path

import pytest

import bootguard_check
import find_options
import gen_setup_var as gsv
import ifr_to_json

FIX = Path(__file__).parent / "fixtures" / "sample_ifr.txt"


@pytest.fixture(scope="module")
def qs():
    return ifr_to_json.to_jsonable(ifr_to_json.parse(FIX.read_text()))


def by_prompt(qs, p):
    return next(q for q in qs if q["prompt"] == p)


def test_ifr_parse_fields(qs):
    assert len(qs) == 9
    t = by_prompt(qs, "Intel(R) Turbo Boost Technology")
    assert (t["varstore_name"], t["offset"], t["size"], t["default"]) == ("CpuSetup", 0x10, 1, 1)
    assert t["hidden"] is None
    pl1 = by_prompt(qs, "Power Limit 1")
    assert (pl1["size"], pl1["max"], pl1["default"], pl1["hidden"]) == (2, 1000, 0, "always")
    bar = by_prompt(qs, "Re-Size BAR Support")
    assert (bar["varstore_name"], bar["hidden"], bar["grayed"], bar["default"]) == ("Setup", None, "conditional", 0)
    assert by_prompt(qs, "Overclocking Lock")["default"] == 1
    cstate = by_prompt(qs, "Package C State Limit")
    assert [o["value"] for o in cstate["options"] if o["default"]] == [255]


def test_find_options(qs):
    hits = find_options.search(qs, ["power limit|c state"], hidden_only=True)
    assert {q["prompt"] for q in hits} == {"Power Limit 1", "Package C State Limit"}
    assert "CpuSetup@0x20" in find_options.fmt(hits[0])


ALLOW = gsv.load_allowlist()


def test_safe_change_generates_scripts(qs, tmp_path):
    ch = gsv.plan(qs, ["Package C State Limit=C10", "0x2001=Enabled", "Power Limit 1=360"], ALLOW)
    assert [c.value for c in ch] == [8, 1, 360]
    out = gsv.render(ch, "1.28.0")
    assert "setup_var.efi 0x30 0x8 -s 0x1 -n CpuSetup -i 0x2" in out["apply.nsh"]
    assert "setup_var.efi 0x123 -s 0x1 -n Setup -i 0x1" in out["read.nsh"]
    assert "setup_var.efi 0x30 0xff -s 0x1 -n CpuSetup" in out["revert.nsh"]
    assert "setup_var.efi 0x20 0x0 -s 0x2 -n CpuSetup" in out["revert.nsh"]
    assert "1.28.0" in out["apply.nsh"]


@pytest.mark.parametrize(
    "setting,msg",
    [
        ("BIOS Guard=Disabled", "deny"),
        ("Core Voltage Offset=100", "deny"),
        ("Memory Frequency=4800", "deny"),
        ("Undervolt Protection=Disabled", "advanced"),
        ("Package C State Limit=C99", "not one of"),
        ("Power Limit 1=5000", "outside"),
        ("Nope=1", "no option"),
        ("Package C State Limit", "expects"),
    ],
)
def test_refusals(qs, setting, msg):
    with pytest.raises(gsv.Refused, match=msg):
        gsv.plan(qs, [setting], ALLOW)


def test_advanced_needs_flag_and_deny_ignores_force(qs):
    ch = gsv.plan(qs, ["Undervolt Protection=Disabled"], ALLOW, allow_advanced=True)
    assert ch[0].tier == "advanced" and ch[0].value == 0
    with pytest.raises(gsv.Refused, match="deny"):
        gsv.plan(qs, ["BIOS Guard=Disabled"], ALLOW, allow_advanced=True, force=True)


def test_duplicate_offset_refused(qs):
    with pytest.raises(gsv.Refused, match="twice"):
        gsv.plan(qs, ["0x1003=C10", "Package C State Limit=Auto"], ALLOW)


def test_cli_writes_files(qs, tmp_path):
    j = tmp_path / "s.json"
    j.write_text(json.dumps(qs))
    rc = gsv.main([str(j), "--bios-version", "1.28.0", "--set", "0x1001=Disabled", "-o", str(tmp_path / "usb")])
    assert rc == 0
    assert (tmp_path / "usb" / "apply.nsh").read_bytes().count(b"\r\n") > 3
    assert gsv.main([str(j), "--bios-version", "x", "--set", "BIOS Guard=0", "-o", str(tmp_path / "u2")]) == 2


def test_bootguard_decode():
    d = bootguard_check.decode((1 << 32) | (1 << 6) | (1 << 5) | 1)
    assert d["verified_boot"] and d["measured_boot"] and d["boot_guard_capable"]
    assert bootguard_check.verdict(d).startswith("ENFORCED")
    assert bootguard_check.verdict(bootguard_check.decode(0)).startswith("No Boot Guard")


def test_meinfo_parse():
    text = "FW Version   16.1.25.2124\nBoot Guard                                Enabled\nVerified Boot                             Yes\n"
    rows = bootguard_check.parse_meinfo(text)
    assert rows == {"Boot Guard": "Enabled", "Verified Boot": "Yes"}
