"""The sense -> classify -> control -> actuate loop, with safety fallbacks."""

from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from .actuators import Actuator
from .ambient import FoulingMonitor
from .controller import Controller
from .model import Decision, Sample
from .sensors import SensorSource
from .telemetry import CsvLogger

log = logging.getLogger("x17tune")

MAX_CONSECUTIVE_ERRORS = 5
BLIND_LIMIT_S = 30.0  # no temperatures at all for this long -> restore stock behavior
FOULING_EVERY_S = 600.0


@dataclass
class DaemonStats:
    ticks: int = 0
    errors: int = 0
    actuations: int = 0
    last_decision: Decision | None = None


class Daemon:
    def __init__(
        self,
        sensors: SensorSource,
        controller: Controller,
        actuator: Actuator,
        logger: CsvLogger | None = None,
        fouling: FoulingMonitor | None = None,
        tick_s: float = 1.0,
        sleep=time.sleep,
    ):
        self.sensors = sensors
        self.ctl = controller
        self.act = actuator
        self.logger = logger
        self.fouling = fouling
        self.tick_s = tick_s
        self.sleep = sleep
        self.stats = DaemonStats()
        self._stop = False
        self._last_seen_temp: float | None = None
        self._last_fouling = -1e9
        self._blind = False

    def stop(self, *_):
        self._stop = True

    def _check_blind(self, s: Sample) -> bool:
        if s.cpu_temp is not None or s.gpu_temp is not None:
            self._last_seen_temp = s.t
            if self._blind:
                log.warning("temperatures are back; resuming control")
            self._blind = False
            return False
        if self._last_seen_temp is None:
            self._last_seen_temp = s.t
        if s.t - self._last_seen_temp >= BLIND_LIMIT_S and not self._blind:
            log.error("no temperature readings for %.0f s - restoring stock behavior", BLIND_LIMIT_S)
            self.act.restore()
            self._blind = True
        return self._blind

    def tick(self) -> Decision | None:
        s = self.sensors.read()
        d = self.ctl.step(s)
        self.stats.last_decision = d
        if hasattr(self.sensors, "feedback"):
            self.sensors.feedback(d)  # simulated plant follows the decision
        if not self._check_blind(s):
            before = self.act.last
            self.act.apply(d, s)
            if before is None or before.key() != d.key():
                self.stats.actuations += 1
                log.info(
                    "%s | %s | PL1 %.0f W PL2 %.0f W EPP %d | GPU %s MHz slot %s | fan %s | %s",
                    d.workload.value,
                    d.thermal_mode.value,
                    d.cpu_pl1_w,
                    d.cpu_pl2_w,
                    d.cpu_epp,
                    d.gpu_clock_max_mhz or "auto",
                    d.gpu_profile or "-",
                    d.fan_boost if d.fan_boost is not None else "EC",
                    "; ".join(d.notes),
                )
        if self.logger:
            self.logger.log(s, d)
        if self.fouling and s.t - self._last_fouling >= FOULING_EVERY_S and self.ctl.last_estimate:
            self._last_fouling = s.t
            self.fouling.record(self.ctl.last_estimate)
        self.stats.ticks += 1
        return d

    def run(self, seconds: float | None = None) -> DaemonStats:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self.stop)
            except (ValueError, OSError):  # not main thread / unsupported
                pass
        start = time.monotonic()
        consecutive = 0
        try:
            while not self._stop:
                t0 = time.monotonic()
                try:
                    self.tick()
                    consecutive = 0
                except Exception as e:  # noqa: BLE001
                    consecutive += 1
                    self.stats.errors += 1
                    log.exception("tick failed (%d in a row): %s", consecutive, e)
                    if consecutive >= MAX_CONSECUTIVE_ERRORS:
                        log.error("too many errors - restoring stock behavior and exiting")
                        break
                if seconds is not None and time.monotonic() - start >= seconds:
                    break
                self.sleep(max(0.0, self.tick_s - (time.monotonic() - t0)))
        finally:
            log.info("stopping: restoring stock behavior")
            self.act.restore()
            if self.logger:
                self.logger.close()
        return self.stats


def default_state_dir() -> Path:
    import os
    import sys

    if sys.platform.startswith("win"):
        base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        return base / "x17tune"
    return Path("/var/lib/x17tune") if os.geteuid() == 0 else Path.home() / ".local/state/x17tune"
