"""Trapezoidal energy integration for computed power sensors."""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class EnergyAccumulator:
    """Accumulates energy (kWh) from instantaneous power (W) readings
    using trapezoidal integration over time."""

    def __init__(self, initial_kwh: float = 0.0) -> None:
        self._total_kwh: float = initial_kwh
        self._prev_power_w: float | None = None
        self._prev_time: datetime | None = None

    @property
    def total_kwh(self) -> float:
        return round(self._total_kwh, 4)

    @total_kwh.setter
    def total_kwh(self, value: float) -> None:
        self._total_kwh = value

    def accumulate(self, power_w: float, now: datetime) -> float:
        """Add a new power sample. Returns updated total kWh.

        Uses trapezoidal rule: energy = (P_prev + P_now) / 2 * dt_hours
        Negative power values are clamped to zero.
        """
        power_w = max(0.0, power_w)

        if self._prev_power_w is not None and self._prev_time is not None:
            dt_seconds = (now - self._prev_time).total_seconds()
            if 0 < dt_seconds < 3600:  # sanity: ignore gaps > 1 hour
                dt_hours = dt_seconds / 3600.0
                avg_power = (self._prev_power_w + power_w) / 2.0
                self._total_kwh += avg_power * dt_hours / 1000.0

        self._prev_power_w = power_w
        self._prev_time = now
        return self.total_kwh

    def reset_sample(self) -> None:
        """Reset previous sample (e.g. after a long gap)."""
        self._prev_power_w = None
        self._prev_time = None
