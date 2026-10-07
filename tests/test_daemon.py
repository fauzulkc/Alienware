from x17tune.actuators.base import Actuator, DryRunner
from x17tune.controller import Controller
from x17tune.daemon import BLIND_LIMIT_S, MAX_CONSECUTIVE_ERRORS, Daemon
from x17tune.model import Sample, Workload


class FakeActuator(Actuator):
    def __init__(self):
        super().__init__(DryRunner())
        self.applied = 0
        self.restored = 0

    def set_thermal_mode(self, mode): ...
    def set_cpu(self, d, s, changed): ...
    def set_gpu_clock(self, m): ...
    def set_gpu_profile(self, slot): ...
    def set_fan_boost(self, b): ...

    def apply(self, d, s):
        self.applied += 1
        super().apply(d, s)

    def restore(self):
        self.restored += 1
        self.last = None


class Seq:
    def __init__(self, fn):
        self.t = 0.0
        self.fn = fn

    def read(self):
        self.t += 1
        return self.fn(self.t)


def test_blind_sensors_restore_stock(cfg):
    act = FakeActuator()
    d = Daemon(Seq(lambda t: Sample(t=t)), Controller(cfg, forced_workload=Workload.BALANCED), act, tick_s=0)
    for _ in range(int(BLIND_LIMIT_S) + 5):
        d.tick()
    assert act.restored == 1
    assert act.applied < BLIND_LIMIT_S + 1


def test_errors_restore_and_exit(cfg):
    def boom(t):
        raise RuntimeError("sensor died")

    act = FakeActuator()
    d = Daemon(Seq(boom), Controller(cfg), act, tick_s=0, sleep=lambda _: None)
    stats = d.run()
    assert stats.errors == MAX_CONSECUTIVE_ERRORS
    assert act.restored == 1


def test_run_for_seconds_restores_on_exit(cfg, tmp_path):
    from x17tune.telemetry import CsvLogger, summarize

    act = FakeActuator()
    log = CsvLogger(tmp_path / "t.csv")
    d = Daemon(
        Seq(lambda t: Sample(t=t, cpu_temp=60, gpu_temp=55, cpu_power=20, gpu_power=30)),
        Controller(cfg),
        act,
        logger=log,
        tick_s=0,
        sleep=lambda _: None,
    )
    stats = d.run(seconds=0.05)
    assert stats.ticks >= 1 and act.restored == 1
    assert summarize(tmp_path / "t.csv")["rows"] == stats.ticks
