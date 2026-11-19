"""Conservative per-station rain accounting, independent of Home Assistant."""

from dataclasses import dataclass


@dataclass
class RainTracker:
    """Keep a monotonic logical total across resets and 16-bit rollovers.

    Nonzero decreases (including missed byte carries) are never adopted. Zero
    resets and near-boundary rollovers need three observations at least five
    seconds apart. Burst copies cannot confirm a reset. An unobserved nonzero
    reset requires reconfiguration; undercounting is preferable to a false spike.
    Only accepted baselines and totals are persisted, never pending evidence.
    """

    raw: int | None = None
    total: int = 0
    initialized: bool = False
    _pending: int = 0
    _pending_raw: int = 0
    _pending_time: float = 0

    def update(self, tips: int, now: float) -> float:
        """Return safe cumulative mm for one distinct received measurement."""
        if self.raw is None:
            if not self.initialized:
                self.total = tips
            self.raw = tips
            self.initialized = True
        elif tips >= self.raw:
            self.total += tips - self.raw
            self.raw = tips
            self._pending = 0
        elif tips == 0 or (self.raw >= 0xFF00 and tips <= 0xFF):
            if self._pending and tips < self._pending_raw:
                self._pending = 0
            if not self._pending or now - self._pending_time >= 5:
                self._pending += 1
                self._pending_time = now
                self._pending_raw = tips
            if self._pending >= 3:
                if self.raw >= 0xFF00:
                    self.total += 65536 - self.raw + tips
                self.raw = tips
                self._pending = 0
        else:
            self._pending = 0
        return round(self.total * 0.233, 3)

    def replace_station(self) -> None:
        """Rebaseline a replacement ID without counting its historical rain."""
        self.raw = None
        self._pending = 0
