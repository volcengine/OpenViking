# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Server-side DingTalk MCP identities."""

from typing import Dict, List, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DingTalkMCPServerConfig(BaseModel):
    """One Streamable HTTP MCP endpoint.

    Endpoint URLs and headers may contain credentials, so their repr is hidden.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    url: str = Field(repr=False)
    headers: Dict[str, str] = Field(default_factory=dict, repr=False)

    @model_validator(mode="after")
    def _validate(self) -> "DingTalkMCPServerConfig":
        try:
            parsed = urlsplit(self.url)
            valid = (
                parsed.scheme in {"https", "http"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not parsed.fragment
            )
            _ = parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("DingTalk MCP URL must be an HTTP(S) URL without userinfo or fragment")
        return self


class DingTalkIdentityConfig(BaseModel):
    """Named server-side identity spanning the three read-only MCP services."""

    model_config = ConfigDict(hide_input_in_errors=True)

    label: str = ""
    doc: DingTalkMCPServerConfig = Field(repr=False)
    sheets: Optional[DingTalkMCPServerConfig] = Field(default=None, repr=False)
    ai_table: Optional[DingTalkMCPServerConfig] = Field(default=None, repr=False)


class DingTalkConfig(BaseModel):
    """DingTalk identities selectable by import requests."""

    model_config = ConfigDict(hide_input_in_errors=True)

    identities: Dict[str, DingTalkIdentityConfig] = Field(default_factory=dict, repr=False)

    @model_validator(mode="after")
    def _validate_names(self) -> "DingTalkConfig":
        invalid = [name for name in self.identities if not name or name.strip() != name]
        if invalid:
            raise ValueError("DingTalk identity names must be non-empty and have no outer spaces")
        return self

    def identity_options(self) -> List[Dict[str, str]]:
        """Return the public identity fields safe for API and UI responses."""
        return [
            {"name": name, "label": identity.label or name}
            for name, identity in self.identities.items()
        ]
