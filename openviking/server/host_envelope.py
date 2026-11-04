# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Let a client declare which host envelopes wrap its requests.

A plugin or bridge knows which blocks it injected, so its declaration beats guessing
from the text. ``X-OpenViking-Host-Envelope: botmux,claude-code`` tries exactly those
envelopes, in that order; ``none`` turns normalization off for the request. Without the
header the server falls back to ``server.host_envelope``.
"""

from typing import List, Optional

from fastapi import Request

from openviking.server.config import HostEnvelopeConfig
from openviking.utils.host_envelope import HostEnvelope
from openviking_cli.exceptions import InvalidArgumentError

HOST_ENVELOPE_HEADER = "X-OpenViking-Host-Envelope"


def declared_host_envelopes(request: Request) -> Optional[List[HostEnvelope]]:
    """Envelopes the client declared, ``[]`` for ``none``, or ``None`` if it declared nothing."""
    value = request.headers.get(HOST_ENVELOPE_HEADER)
    if value is None:
        return None
    names = [name.strip() for name in value.split(",") if name.strip()]
    if [name.lower() for name in names] == ["none"]:
        return []
    config = getattr(request.app.state, "config", None)
    envelope_config = getattr(config, "host_envelope", None) or HostEnvelopeConfig()
    try:
        return envelope_config.lookup(names)
    except ValueError as exc:
        raise InvalidArgumentError(f"{HOST_ENVELOPE_HEADER}: {exc}") from exc
