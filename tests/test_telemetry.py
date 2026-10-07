import csv

from x17tune.telemetry import FIELDS, summarize


def test_summarize_counts_throttle_events(tmp_path):
    p = tmp_path / "t.csv"
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        pattern = [0, 1, 1, 0, 1, 0, 0]
        for i, thr in enumerate(pattern * 100):
            w.writerow({"t": i * 1.0, "cpu_temp": 80 + thr * 20, "gpu_throttling": thr, "cpu_throttling": 0})
    s = summarize(p)
    hours = (700 - 1) / 3600
    assert s["gpu_throttling_events_per_h"] == round(200 / hours, 1)
    assert s["cpu_throttling_events_per_h"] == 0
    assert s["cpu_temp_max"] == 100
