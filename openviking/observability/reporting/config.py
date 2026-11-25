"""Reporting settings from the server configuration loaded from ov.conf."""

from __future__ import annotations

from copy import deepcopy


def load_configuration(settings) -> dict:
    """Return an isolated snapshot of the reporting sections in ov.conf."""
    configuration = {"kafka": settings.kafka, "telemetry": settings.telemetry}
    if settings.file_log:
        configuration["file_log"] = settings.file_log
    return deepcopy(configuration)
