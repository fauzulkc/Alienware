from x17tune.climate import BandSelector


def test_band_hysteresis(cfg):
    sel = BandSelector(cfg.bands, hysteresis_c=1.0, initial_ambient_c=30)
    assert sel.band.name == "warm"
    assert sel.update(33.5).name == "warm"  # within hysteresis of the 33 °C edge
    assert sel.update(34.2).name == "hot"
    assert sel.update(32.5).name == "hot"  # must drop below 32 to go back
    assert sel.update(31.9).name == "warm"
    assert sel.update(45).name == "extreme"
    assert sel.update(20).name == "mild"
