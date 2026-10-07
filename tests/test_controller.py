import pytest

from x17tune.ambient import AmbientEstimator
from x17tune.controller import Controller
from x17tune.model import Sample, Workload
from x17tune.sim import ThermalPlant, run_closed_loop


def closed_loop(cfg, amb, wl, cpu_w, gpu_w, minutes=40, manual=None):
    plant = ThermalPlant(ambient_c=amb, seed=7)
    ctl = Controller(cfg, forced_workload=wl, estimator=AmbientEstimator(cfg.default_ambient_c, manual_c=manual))
    return run_closed_loop(plant, ctl, lambda t: (cpu_w, gpu_w, {}), minutes * 60)


@pytest.mark.parametrize("amb", [25.0, 32.0, 38.0])
@pytest.mark.parametrize(
    "wl,cpu_w,gpu_w",
    [(Workload.GAMING, 45, 140), (Workload.AI_INFERENCE, 25, 130), (Workload.CREATOR, 80, 10)],
)
def test_temps_held_and_no_limit_cycle(cfg, amb, wl, cpu_w, gpu_w):
    res = closed_loop(cfg, amb, wl, cpu_w, gpu_w)
    late = res[20 * 60 :]
    # Steady state: within target + 2 °C (sensor noise included), no throttling.
    assert max(s.cpu_temp for s, _ in late) <= cfg.cpu_target_c + 2
    assert max(s.gpu_temp for s, _ in late) <= cfg.gpu_target_c + 2
    assert not any(s.cpu_throttling or s.gpu_throttling for s, _ in late)
    # No limit cycling: settings change at most a couple of times in the last 20 min.
    keys = [d.key() for _, d in late]
    assert sum(a != b for a, b in zip(keys, keys[1:])) <= 2


def test_sustained_power_drops_as_room_heats_up(cfg):
    watts = []
    for amb in (25.0, 32.0, 38.0):
        res = closed_loop(cfg, amb, Workload.GAMING, 45, 140, manual=amb)
        late = res[20 * 60 :]
        watts.append(sum(s.cpu_power + s.gpu_power for s, _ in late) / len(late))
    assert watts[0] > watts[1] > watts[2]


def test_hot_band_switches_to_efficient_gpu_slot(cfg):
    ctl = Controller(cfg, forced_workload=Workload.GAMING, estimator=AmbientEstimator(manual_c=36))
    d = ctl.step(Sample(t=0, cpu_temp=60, gpu_temp=60, cpu_power=30, gpu_power=100))
    assert d.band == "hot"
    assert d.gpu_profile == cfg.profiles[Workload.GAMING].gpu_profile_efficient
    assert d.cpu_pl1_w < cfg.profiles[Workload.GAMING].cpu_pl1_w


def test_untrusted_estimate_uses_default_band(cfg):
    ctl = Controller(cfg, forced_workload=Workload.BALANCED)
    d = ctl.step(Sample(t=0, cpu_temp=50, gpu_temp=45, cpu_power=10, gpu_power=10))
    assert d.band == "warm"  # default_ambient_c = 32


def test_battery_overrides_forced_workload(cfg):
    ctl = Controller(cfg, forced_workload=Workload.GAMING)
    d = ctl.step(Sample(t=0, cpu_temp=50, gpu_temp=45, on_ac=False))
    assert d.workload is Workload.BATTERY
    assert d.cpu_pl1_w <= cfg.profiles[Workload.BATTERY].cpu_pl1_w


def test_emergency_fan_boost_with_hysteresis(cfg):
    ctl = Controller(cfg, forced_workload=Workload.QUIET, estimator=AmbientEstimator(manual_c=25))
    t = 0
    d = None
    for _ in range(30):
        d = ctl.step(Sample(t=t, cpu_temp=cfg.cpu_target_c + 6, gpu_temp=50))
        t += 1
    assert d.fan_boost == 255
    for _ in range(60):
        d = ctl.step(Sample(t=t, cpu_temp=cfg.cpu_target_c - 0.5, gpu_temp=50))
        t += 1
    assert d.fan_boost == 255  # not yet below the clear threshold
    for _ in range(60):
        d = ctl.step(Sample(t=t, cpu_temp=cfg.cpu_target_c - 5, gpu_temp=50))
        t += 1
    assert d.fan_boost == cfg.profiles[Workload.QUIET].fan_boost


def test_missing_sensors_are_open_loop(cfg):
    ctl = Controller(cfg, forced_workload=Workload.CREATOR)
    d = ctl.step(Sample(t=0))
    assert any("open-loop" in n for n in d.notes)
    assert d.cpu_pl1_w <= cfg.profiles[Workload.CREATOR].cpu_pl1_w


def test_limits_always_respected(cfg):
    ctl = Controller(cfg, forced_workload=Workload.CREATOR, estimator=AmbientEstimator(manual_c=15))
    for t in range(200):
        d = ctl.step(Sample(t=t, cpu_temp=120, gpu_temp=120, cpu_power=200, gpu_power=300))
        lo, hi = cfg.limits.cpu_pl1_w
        assert lo <= d.cpu_pl1_w <= hi
        assert d.cpu_pl2_w >= d.cpu_pl1_w
        if d.gpu_clock_max_mhz is not None:
            assert cfg.limits.gpu_clock_mhz[0] <= d.gpu_clock_max_mhz <= cfg.limits.gpu_clock_mhz[1]
