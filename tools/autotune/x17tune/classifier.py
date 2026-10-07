"""Workload classification: process rules first, telemetry heuristics second.

Classes are debounced: a new class must persist for `hold_s` before it is
reported, so a single alt-tab or a one-second spike doesn't flip profiles.
Switching to BATTERY is immediate (safety: unplugged laptops must drop power
right away).
"""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from .model import Sample, Workload

GPU_BUSY_UTIL = 50.0  # % average over the window
WINDOW_S = 15.0


def _norm(names: Iterable[str]) -> frozenset[str]:
    return frozenset(n.lower().removesuffix(".exe") for n in names)


class RuleSet:
    def __init__(self, rules: dict[str, Any]):
        self.by_class: dict[Workload, frozenset[str]] = {}
        for w in (Workload.AI_INFERENCE, Workload.AI_TRAINING, Workload.GAMING, Workload.CREATOR, Workload.QUIET):
            self.by_class[w] = _norm(rules.get(w.value, []) or [])
        self.ai_if_gpu_busy = _norm(rules.get("ai_if_gpu_busy", []) or [])
        self.game_path_markers = tuple(m.lower() for m in rules.get("game_path_markers", []) or [])

    def foreground_class(self, s: Sample) -> Workload | None:
        if s.foreground:
            for w, names in self.by_class.items():
                if s.foreground in names:
                    return w
        if s.foreground_path and any(m in s.foreground_path for m in self.game_path_markers):
            return Workload.GAMING
        return None

    def background_ai(self, s: Sample, gpu_busy: bool) -> Workload | None:
        if not gpu_busy:
            return None
        if s.processes & self.by_class[Workload.AI_TRAINING]:
            return Workload.AI_TRAINING
        if s.processes & self.by_class[Workload.AI_INFERENCE]:
            return Workload.AI_INFERENCE
        if s.processes & self.ai_if_gpu_busy:
            return Workload.AI_INFERENCE
        return None


class Classifier:
    def __init__(self, rules: dict[str, Any], hold_s: float = 10.0):
        self.rules = RuleSet(rules)
        self.hold_s = hold_s
        self.window: deque[Sample] = deque()
        self.current: Workload = Workload.BALANCED
        self._candidate: Workload | None = None
        self._candidate_since: float = 0.0

    def _avg(self, attr: str) -> float:
        vals = [getattr(x, attr) or 0.0 for x in self.window]
        return sum(vals) / len(vals) if vals else 0.0

    def raw(self, s: Sample) -> Workload:
        """Instantaneous (un-debounced) classification."""
        if not s.on_ac:
            return Workload.BATTERY
        gpu_util = self._avg("gpu_util")
        cpu_util = self._avg("cpu_util")
        gpu_busy = gpu_util >= GPU_BUSY_UTIL

        fg = self.rules.foreground_class(s)
        if fg is Workload.AI_INFERENCE and gpu_util >= 85:
            return Workload.AI_TRAINING  # a "chat" tool pegging the GPU for the whole window is batch work
        if fg is not None:
            return fg

        bg = self.rules.background_ai(s, gpu_busy)
        if bg is Workload.AI_INFERENCE and gpu_util >= 85 and cpu_util < 30:
            return Workload.AI_TRAINING
        if bg is not None:
            return bg

        # Telemetry heuristics: games keep the CPU busy feeding frames, compute
        # jobs leave it mostly idle.
        if gpu_busy:
            return Workload.GAMING if cpu_util >= 20 else Workload.AI_INFERENCE
        if cpu_util >= 60:
            return Workload.CREATOR
        if cpu_util < 15 and gpu_util < 15:
            return Workload.QUIET
        return Workload.BALANCED

    def update(self, s: Sample) -> Workload:
        self.window.append(s)
        while self.window and s.t - self.window[0].t > WINDOW_S:
            self.window.popleft()

        cand = self.raw(s)
        if cand == self.current:
            self._candidate = None
            return self.current
        if cand is Workload.BATTERY:
            self.current, self._candidate = cand, None
            return self.current
        if cand != self._candidate:
            self._candidate, self._candidate_since = cand, s.t
        elif s.t - self._candidate_since >= self.hold_s:
            self.current, self._candidate = cand, None
        return self.current
