"""Optional remote event reporting; Kafka dependencies are loaded only on enablement."""

from .runtime import start_reporting, stop_reporting

__all__ = ["start_reporting", "stop_reporting"]
