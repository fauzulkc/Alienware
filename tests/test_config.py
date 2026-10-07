import copy

import pytest
import yaml
from importlib import resources

from x17tune import config
from x17tune.model import Workload


def _doc():
    return yaml.safe_load(resources.files("x17tune").joinpath("config", "profiles.yaml").read_text())


def test_packaged_config_loads(cfg):
    assert set(cfg.profiles) == set(Workload)
    assert cfg.bands[0].max_c < cfg.bands[-1].max_c
    for p in cfg.profiles.values():
        assert cfg.limits.cpu_pl1_w[0] <= p.cpu_pl1_floor_w <= p.cpu_pl1_w <= cfg.limits.cpu_pl1_w[1]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["profiles"].pop("gaming"),
        lambda d: d["profiles"]["gaming"].update(cpu_epp=150),
        lambda d: d["profiles"]["gaming"].update(cpu_pl2_w=10),
        lambda d: d["profiles"]["gaming"].update(gpu_profile=9),
        lambda d: d["profiles"]["gaming"].update(cpu_pl1_floor_w=80),
        lambda d: d["climate"]["bands"].reverse(),
        lambda d: d["climate"]["bands"][0].update(power_scale=2.0),
        lambda d: d["limits"].update(cpu_pl1_w=[90, 10]),
    ],
)
def test_invalid_configs_rejected(mutate):
    d = copy.deepcopy(_doc())
    mutate(d)
    with pytest.raises(config.ConfigError):
        config.parse(d, {})
