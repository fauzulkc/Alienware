"""Map the ambient estimate to a climate band, with hysteresis."""

from __future__ import annotations

from .config import Band


class BandSelector:
    def __init__(self, bands: tuple[Band, ...], hysteresis_c: float = 1.0, initial_ambient_c: float | None = None):
        self.bands = bands
        self.hyst = hysteresis_c
        self.idx = self._raw_index(initial_ambient_c) if initial_ambient_c is not None else 0

    def _raw_index(self, ambient_c: float) -> int:
        for i, b in enumerate(self.bands):
            if ambient_c <= b.max_c:
                return i
        return len(self.bands) - 1

    def update(self, ambient_c: float) -> Band:
        raw = self._raw_index(ambient_c)
        if raw > self.idx:
            # Moving to a hotter band: must clear the current band's top by hyst.
            if ambient_c > self.bands[self.idx].max_c + self.hyst:
                self.idx = raw
        elif raw < self.idx:
            # Moving to a cooler band: must drop below the lower band's top by hyst.
            if ambient_c < self.bands[raw].max_c - self.hyst:
                self.idx = raw
        return self.bands[self.idx]

    @property
    def band(self) -> Band:
        return self.bands[self.idx]
