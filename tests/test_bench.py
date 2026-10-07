from ai_bench import parse_llama_bench
from compare import group_results
from gpu_curve_search import knee

LLAMA = """
| model                          |       size |     params | backend    | ngl |          test |                  t/s |
| ------------------------------ | ---------: | ---------: | ---------- | --: | ------------: | -------------------: |
| llama 8B Q4_K - Medium         |   4.58 GiB |     8.03 B | CUDA       |  99 |         pp512 |      2210.52 ± 12.04 |
| llama 8B Q4_K - Medium         |   4.58 GiB |     8.03 B | CUDA       |  99 |         tg256 |         71.34 ± 0.22 |
"""


def test_parse_llama_bench():
    assert parse_llama_bench(LLAMA) == {"metric": "tg_tok_per_s", "value": 71.34, "pp_tok_per_s": 2210.52}


def test_knee_picks_highest_clock_near_best_efficiency():
    pts = [
        {"clock": 1200, "per_watt": 0.300},
        {"clock": 1500, "per_watt": 0.310},
        {"clock": 1600, "per_watt": 0.298},
        {"clock": 1800, "per_watt": 0.250},
    ]
    assert knee(pts, 5.0)["clock"] == 1600
    assert knee([], 5.0) is None


def test_group_results():
    g = group_results(
        [
            {"kind": "matmul", "label": "stock", "value": 20.0, "per_watt": 0.15, "room_c": 33},
            {"kind": "matmul", "label": "stock", "value": 22.0, "per_watt": 0.17, "room_c": 33},
            {"kind": "matmul", "label": "x17tune", "value": 21.0, "per_watt": 0.21, "room_c": 33},
        ]
    )
    assert g[("matmul", "stock")]["value"] == 21.0
    assert g[("matmul", "x17tune")]["runs"] == 1
