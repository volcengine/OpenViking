# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Selectable model-output protocols for the memory extraction loop."""

from openviking.session.memory.extraction_output_protocol.base import (
    ExtractionOutputContext,
    ExtractionOutputProtocol,
)
from openviking.session.memory.extraction_output_protocol.json_protocol import (
    JsonExtractionOutputProtocol,
)
from openviking.session.memory.extraction_output_protocol.python_protocol import (
    PythonExtractionOutputProtocol,
)


def create_extraction_output_protocol(output_format: str) -> ExtractionOutputProtocol:
    if output_format == "json":
        return JsonExtractionOutputProtocol()
    if output_format == "python":
        return PythonExtractionOutputProtocol()
    raise ValueError(f"Unsupported memory extraction output format: {output_format}")


__all__ = [
    "ExtractionOutputContext",
    "ExtractionOutputProtocol",
    "JsonExtractionOutputProtocol",
    "PythonExtractionOutputProtocol",
    "create_extraction_output_protocol",
]
