from x17tune.classifier import Classifier
from x17tune.model import Sample, Workload

RULES = {
    "ai_inference": ["ollama"],
    "ai_if_gpu_busy": ["python"],
    "gaming": ["cs2"],
    "creator": ["blender"],
    "game_path_markers": ["steamapps/common"],
}


def feed(c, n, **kw):
    w = None
    for i in range(n):
        w = c.update(Sample(t=feed.t, **kw))
        feed.t += 1
    return w


feed.t = 0.0


def test_battery_switch_is_immediate():
    c = Classifier(RULES, hold_s=10)
    assert feed(c, 1, on_ac=False) is Workload.BATTERY


def test_hold_time_debounces():
    c = Classifier(RULES, hold_s=10)
    feed(c, 20, cpu_util=30, gpu_util=30)  # balanced
    assert feed(c, 5, foreground="cs2", gpu_util=90, cpu_util=40) is Workload.BALANCED
    assert feed(c, 7, foreground="cs2", gpu_util=90, cpu_util=40) is Workload.GAMING


def test_game_by_install_path():
    c = Classifier(RULES, hold_s=0)
    assert feed(c, 2, foreground="unknown", foreground_path="d:/steamapps/common/x/x.exe") is Workload.GAMING


def test_python_only_counts_as_ai_when_gpu_busy():
    c = Classifier(RULES, hold_s=0)
    assert feed(c, 20, processes=frozenset({"python"}), gpu_util=5, cpu_util=5) is Workload.QUIET
    assert feed(c, 20, processes=frozenset({"python"}), gpu_util=70, cpu_util=10) is Workload.AI_INFERENCE


def test_pegged_gpu_ai_becomes_training():
    c = Classifier(RULES, hold_s=0)
    assert feed(c, 20, processes=frozenset({"ollama"}), gpu_util=98, cpu_util=10) is Workload.AI_TRAINING


def test_heuristics():
    c = Classifier(RULES, hold_s=0)
    assert feed(c, 20, gpu_util=90, cpu_util=40) is Workload.GAMING
    assert feed(c, 20, gpu_util=90, cpu_util=5) is Workload.AI_INFERENCE
    assert feed(c, 20, gpu_util=5, cpu_util=90) is Workload.CREATOR
