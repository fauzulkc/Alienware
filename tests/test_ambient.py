import random

import pytest

from x17tune.ambient import AmbientEstimate, AmbientEstimator, FoulingMonitor
from x17tune.sim import PlantParams, ThermalPlant


def run_open_loop(amb, hours=1.5, seed=0, params=None):
    p = ThermalPlant(ambient_c=amb, seed=seed, params=params or PlantParams())
    est = AmbientEstimator(default_ambient_c=32)
    rng = random.Random(seed + int(amb))
    seg_end = 0.0
    cw = gw = 0
    for _ in range(int(hours * 3600)):
        if p.t >= seg_end:
            cw, gw = rng.choice([8, 20, 35, 45, 55]), rng.choice([10, 40, 90, 120, 140])
            seg_end = p.t + rng.uniform(240, 600)
        est.update(p.step(1.0, cw, gw))
    return est.estimate()


@pytest.mark.parametrize("amb", [25.0, 32.0, 38.0])
def test_estimator_converges_within_2c(amb):
    e = run_open_loop(amb)
    assert e.source == "model"
    assert abs(e.ambient_c - amb) <= 2.0
    assert e.std_c <= 2.0


def test_estimator_tracks_thermal_resistance_rise():
    # Clogged fins: heatsink-to-air resistance +30 %. The measured die slopes also
    # contain the (unchanged) die-to-heatsink part, so they rise less - but clearly
    # more than FoulingMonitor's default 10 % alert threshold.
    clean = run_open_loop(32, seed=3)
    dirty = run_open_loop(32, seed=3, params=PlantParams(r_cpu_hs=0.95 * 1.3, r_gpu_hs=0.32 * 1.3))
    assert dirty.r_cpu > clean.r_cpu * 1.10
    assert dirty.r_gpu > clean.r_gpu * 1.10
    assert abs(dirty.ambient_c - 32) <= 2.0


def test_manual_and_file_override(tmp_path):
    assert AmbientEstimator(manual_c=35).estimate().ambient_c == 35
    f = tmp_path / "room.txt"
    f.write_text("36.5\n")
    e = AmbientEstimator(ambient_file=f).estimate()
    assert (e.ambient_c, e.source) == (36.5, "file")
    f.write_text("garbage")
    assert AmbientEstimator(ambient_file=f).estimate().source == "prior"


def _est(r_cpu, r_gpu):
    return AmbientEstimate(32, 0.5, r_cpu, r_gpu, 100, "model")


def test_fouling_alert(tmp_path):
    fm = FoulingMonitor(tmp_path / "f.json", baseline_days=3, recent_days=3, threshold=0.10)
    day = 86400.0
    for i in range(3):
        fm.record(_est(0.90, 0.33), now=i * day)
    assert not fm.report().alert  # still collecting
    for i in range(3, 6):
        fm.record(_est(0.92, 0.34), now=i * day)
    assert not fm.report().alert
    for i in range(6, 9):
        fm.record(_est(1.10, 0.40), now=i * day)
    r = fm.report()
    assert r.alert and "clean" in r.message
    fm.reset()
    assert fm.report().days_of_data == 0


def test_fouling_ignores_unconverged(tmp_path):
    fm = FoulingMonitor(tmp_path / "f.json")
    fm.record(AmbientEstimate(32, 3.5, 1, 1, 100, "model"))
    fm.record(AmbientEstimate(32, 0.5, 1, 1, 100, "prior"))
    assert fm.report().days_of_data == 0
