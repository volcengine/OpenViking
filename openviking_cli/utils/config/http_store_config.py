# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Connection settings for HTTP storage backends."""

import base64
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


def _bearer_token(value: SecretStr) -> str:
    token = value.get_secret_value()
    if not token or any(not 33 <= ord(char) <= 126 for char in token):
        raise ValueError("expected a nonempty bearer token without whitespace")
    return token


class HTTPStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)

    base_url: str
    token: SecretStr
    field_encryption_key: SecretStr | None = None
    http_timeout_seconds: float = Field(default=30.0, gt=0)
    gateway_token: SecretStr = SecretStr("")
    account_id: str = ""
    user_id: str = ""
    actor_peer_id: str = ""
    request_id: str = ""

    @field_validator("field_encryption_key")
    @classmethod
    def validate_field_encryption_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            cls.decode_field_encryption_key(value)
        return value

    @staticmethod
    def decode_field_encryption_key(value: SecretStr) -> bytes:
        message = "field_encryption_key must be base64-encoded 32 bytes"
        try:
            key = base64.b64decode(value.get_secret_value(), validate=True)
        except ValueError:
            raise ValueError(message) from None
        if len(key) != 32:
            raise ValueError(message)
        return key

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise ValueError("expected an HTTP(S) URL without credentials, query or fragment")
        return value.rstrip("/")

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        _bearer_token(value)
        return value

    def headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token.get_secret_value()}",
        }
        fields = {
            "X-Gateway-Token": self.gateway_token.get_secret_value(),
            "X-OpenViking-Account": self.account_id,
            "X-OpenViking-User": self.user_id,
            "X-OpenViking-Actor-Peer": self.actor_peer_id,
            "X-Tt-Logid": self.request_id,
        }
        headers.update((key, value.strip()) for key, value in fields.items() if value.strip())
        return headers
