"""x17tune command line.

    x17tune probe                         read-only: which levers does this machine expose?
    x17tune run                           dry-run: log what it *would* do (default, safe)
    x17tune run --apply                   actually tune (admin/root)
    x17tune simulate --ambient 36 --workload gaming --minutes 20
    x17tune summarize logs/x17tune.csv    averages, maxima, throttle events per hour
    x17tune fouling [--reset]             heatsink-health report / start a new baseline
    x17tune restore                       put stock behavior back now (admin/root)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__, actuators, config
from .ambient import AmbientEstimator, FoulingMonitor
from .classifier import Classifier
from .controller import Controller
from .daemon import Daemon, default_state_dir
from .model import Workload
from .telemetry import CsvLogger, summarize


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--profiles", type=Path, help="custom profiles.yaml")
    p.add_argument("--rules", type=Path, help="custom rules.yaml")
    p.add_argument("--ambient", type=float, help="fix room temperature (°C) instead of estimating it")
    p.add_argument("--ambient-file", type=Path, help="file holding the room temperature (e.g. from a smart thermometer)")
    p.add_argument("--workload", choices=[w.value for w in Workload], help="force a workload class")
    p.add_argument("--log", type=Path, help="telemetry CSV path")
    p.add_argument("--logfile", type=Path, help="also write the event log here (rotating, 4 x 2 MB)")
    p.add_argument("-v", "--verbose", action="store_true")


def _controller(a, cfg) -> Controller:
    est = AmbientEstimator(default_ambient_c=cfg.default_ambient_c, manual_c=a.ambient, ambient_file=a.ambient_file)
    return Controller(
        cfg,
        classifier=Classifier(cfg.rules, hold_s=cfg.controller.hold_s),
        estimator=est,
        forced_workload=Workload(a.workload) if a.workload else None,
    )


def cmd_run(a) -> int:
    from .sensors import for_platform as sensors_for_platform

    cfg = config.load(a.profiles, a.rules)
    state = default_state_dir()
    act = actuators.for_platform(apply=a.apply)
    if a.apply and hasattr(act, "snapshot_power_plan"):
        act.snapshot_power_plan()
        state.mkdir(parents=True, exist_ok=True)
        (state / "powerplan.json").write_text(json.dumps(act.original))
    if not a.apply:
        logging.getLogger("x17tune").warning("DRY RUN - nothing is changed. Add --apply to tune for real.")
    d = Daemon(
        sensors_for_platform(),
        _controller(a, cfg),
        act,
        logger=CsvLogger(a.log) if a.log else None,
        fouling=FoulingMonitor(state / "fouling.json"),
        tick_s=cfg.controller.tick_s,
    )
    stats = d.run(seconds=a.seconds)
    if not a.apply:
        dry = getattr(act.runner, "actions", [])
        print(f"\n{len(dry)} actions would have been taken; last 20:")
        for line in dry[-20:]:
            print("  " + line)
    print(f"ticks={stats.ticks} actuations={stats.actuations} errors={stats.errors}")
    return 0


SIM_LOADS = {  # (CPU W, GPU W) demanded by each simulated workload
    "gaming": (45, 140),
    "ai_inference": (25, 130),
    "ai_training": (30, 140),
    "creator": (80, 10),
    "balanced": (20, 20),
    "quiet": (8, 8),
    "battery": (15, 20),
}


def cmd_simulate(a) -> int:
    from .sensors import SimulatedSensors
    from .sim import ThermalPlant

    cfg = config.load(a.profiles, a.rules)
    wl = a.workload or "gaming"
    a.workload = wl
    cpu_w, gpu_w = SIM_LOADS[wl]
    plant = ThermalPlant(ambient_c=a.room, seed=1)
    sensors = SimulatedSensors(plant, lambda t: (cpu_w, gpu_w, {"on_ac": wl != "battery"}))
    act = actuators.for_platform(apply=False, platform=a.platform)
    logger = CsvLogger(a.log) if a.log else None
    d = Daemon(sensors, _controller(a, cfg), act, logger=logger, tick_s=0, sleep=lambda _: None)
    for _ in range(int(a.minutes * 60)):
        d.tick()
    if logger:
        logger.close()
    last = d.stats.last_decision
    tc, tg = plant.die_temps()
    est = d.ctl.last_estimate
    print(f"simulated {a.minutes:g} min of {wl} in a {a.room:g} °C room ({a.platform} back-end, dry-run)")
    print(f"  final CPU {tc:.1f} °C @ {plant.pc:.0f} W, GPU {tg:.1f} °C @ {plant.pg:.0f} W")
    if est:
        print(f"  ambient estimate {est.ambient_c:.1f} ± {est.std_c:.1f} °C ({est.source}), band {last.band}")
    print(
        f"  decision: {last.thermal_mode.value}, PL1 {last.cpu_pl1_w} W, PL2 {last.cpu_pl2_w} W, EPP {last.cpu_epp}, "
        f"GPU max {last.gpu_clock_max_mhz or 'auto'} MHz, Afterburner slot {last.gpu_profile or '-'}, "
        f"fan boost {last.fan_boost if last.fan_boost is not None else 'EC'}"
    )
    print(f"  {d.stats.actuations} actuation changes; commands that would run:")
    for line in getattr(act.runner, "actions", [])[-12:]:
        print("    " + line)
    return 0


def cmd_probe(a) -> int:
    from .probe import probe

    print(json.dumps(probe(), indent=2, default=str))
    return 0


def cmd_summarize(a) -> int:
    print(json.dumps(summarize(a.csv), indent=2))
    return 0


def cmd_fouling(a) -> int:
    fm = FoulingMonitor(default_state_dir() / "fouling.json")
    if a.reset:
        fm.reset()
        print("baseline reset - the next days of use become the new clean-heatsink baseline")
        return 0
    r = fm.report()
    print(json.dumps(r.__dict__, indent=2))
    return 0


def cmd_restore(a) -> int:
    act = actuators.for_platform(apply=True)
    snap = default_state_dir() / "powerplan.json"
    if hasattr(act, "original") and snap.exists():
        act.original = {k: tuple(v) for k, v in json.loads(snap.read_text()).items()}
    act.restore()
    print("stock behavior restored")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="x17tune", description="Alienware x17 R2 ambient-aware auto-tuner")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="run the tuning loop (dry-run unless --apply)")
    _common(p)
    p.add_argument("--apply", action="store_true", help="actually change settings (needs admin/root)")
    p.add_argument("--seconds", type=float, help="stop after N seconds")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("simulate", help="closed-loop run against the built-in thermal model")
    _common(p)
    p.add_argument("--room", type=float, default=34.0, help="true room temperature of the simulated plant")
    p.add_argument("--minutes", type=float, default=20.0)
    p.add_argument("--platform", choices=["win32", "linux"], default="win32", help="which back-end's commands to show")
    p.set_defaults(fn=cmd_simulate)

    p = sub.add_parser("probe", help="read-only capability report")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("summarize", help="summarize a telemetry CSV")
    p.add_argument("csv", type=Path)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_summarize)

    p = sub.add_parser("fouling", help="heatsink health (dust/humidity) report")
    p.add_argument("--reset", action="store_true", help="start a new baseline after cleaning/repasting")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_fouling)

    p = sub.add_parser("restore", help="restore stock behavior immediately")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_restore)

    a = ap.parse_args(argv)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format=fmt)
    if getattr(a, "logfile", None):
        from logging.handlers import RotatingFileHandler

        a.logfile.parent.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(a.logfile, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter(fmt))
        logging.getLogger().addHandler(h)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
