"""RegionalSignal abstract base class — plugin interface for external signals.

Each plugin exposes a scalar 0-1 risk factor per timestep over the 72h horizon.
Plugins that cannot fetch data return None (signal unavailable).
"""

from abc import ABC, abstractmethod
from typing import Any


class RegionalSignal(ABC):
    """Base class for regional flood signal plugins."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable signal name."""
        ...

    @abstractmethod
    def fetch(self, district_config: dict[str, Any]) -> float | None:
        """Fetch the signal value.

        Args:
            district_config: District configuration dict.

        Returns:
            Scalar 0-1 value, or None if signal is unavailable.
            - 1.0 = perfect agreement / high confidence
            - 0.0 = no agreement / high concern
            - None = data unavailable
        """
        ...
