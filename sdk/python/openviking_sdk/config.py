from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ClientConfig:
    url: str
    api_key: Optional[str]
    account: Optional[str]
    user: Optional[str]
    actor_peer_id: Optional[str]
    timeout: float


def resolve_client_config(
    *,
    url: Optional[str] = None,
    api_key: Optional[str] = None,
    account: Optional[str] = None,
    user: Optional[str] = None,
    actor_peer_id: Optional[str] = None,
    timeout: float = 60.0,
) -> ClientConfig:
    resolved_url = url or os.getenv("OPENVIKING_URL")
    resolved_api_key = api_key or os.getenv("OPENVIKING_API_KEY")
    resolved_account = account or os.getenv("OPENVIKING_ACCOUNT")
    resolved_user = user or os.getenv("OPENVIKING_USER")
    resolved_actor_peer_id = actor_peer_id or os.getenv("OPENVIKING_ACTOR_PEER_ID")

    resolved_timeout = timeout
    if timeout == 60.0 and os.getenv("OPENVIKING_TIMEOUT"):
        resolved_timeout = float(os.getenv("OPENVIKING_TIMEOUT"))

    if not resolved_url:
        raise ValueError("url is required. Pass it explicitly or set OPENVIKING_URL.")

    return ClientConfig(
        url=resolved_url.rstrip("/"),
        api_key=resolved_api_key,
        account=resolved_account,
        user=resolved_user,
        actor_peer_id=resolved_actor_peer_id,
        timeout=resolved_timeout,
    )
