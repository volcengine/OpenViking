# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Redis key construction and reversible expiry identities."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

import xxhash

from .api import ExpiryError, ItemIdentity
from .models import MINUTE_MS, BucketRef, OwnerRole, validate_identity, validate_item_type

HASH_SEED = 0
HASH_NAME = "xxh64"
IDENTITY_ENCODING = "urlsafe_base64"


def encode_component(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExpiryError("invalid_identity", "Identity components must be non-empty strings")
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def decode_component(value: str) -> str:
    if not isinstance(value, str) or not value or "=" in value:
        raise ExpiryError("invalid_identity", "Invalid encoded identity component")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ExpiryError("invalid_identity", "Invalid encoded identity component") from exc
    if not decoded or encode_component(decoded) != value:
        raise ExpiryError("invalid_identity", "Non-canonical encoded identity component")
    return decoded


def encode_item_id(identity: ItemIdentity) -> str:
    validate_identity(identity)
    return ":".join(
        (
            identity.item_type,
            encode_component(identity.account_id),
            encode_component(identity.user_id),
            encode_component(identity.local_id),
        )
    )


def decode_item_id(item_id: str) -> ItemIdentity:
    if not isinstance(item_id, str):
        raise ExpiryError("invalid_identity", "Invalid expiry item ID")
    parts = item_id.split(":")
    if len(parts) != 4:
        raise ExpiryError("invalid_identity", "Invalid expiry item ID")
    validate_item_type(parts[0])
    identity = ItemIdentity(parts[0], *(decode_component(part) for part in parts[1:]))
    validate_identity(identity)
    return identity


def shard_for(item_id: str, shard_count: int) -> int:
    if type(shard_count) is not int or shard_count <= 0:
        raise ValueError("shard_count must be positive")
    return xxhash.xxh64(item_id.encode("utf-8"), seed=HASH_SEED).intdigest() % shard_count


@dataclass(frozen=True)
class ExpiryKeys:
    service_key: str

    def namespace(self) -> str:
        return f"expiry:namespace:{self.service_key}"

    def metadata(self, item_id: str) -> str:
        return f"expiry:metadata:{self.service_key}:{item_id}"

    def source(self, shard_id: int) -> str:
        return f"expiry:publisher:source:{self.service_key}:{shard_id}"

    def control_tag(self, shard_id: int) -> str:
        return f"{{{self.service_key}:{shard_id}}}"

    def progress(self, shard_id: int) -> str:
        return f"expiry:publisher:progress:{self.control_tag(shard_id)}"

    def registry(self, shard_id: int) -> str:
        return f"expiry:consumer:registry:{self.control_tag(shard_id)}"

    def owner(self, role: OwnerRole, shard_id: int, *, item_type: str | None = None) -> str:
        if role is OwnerRole.SCHEDULER:
            if item_type is not None:
                raise ValueError("Scheduler owner must not include an item type")
            return f"expiry:publisher:owner:{self.control_tag(shard_id)}"
        if item_type is None:
            raise ValueError("Callback owner requires an item type")
        validate_item_type(item_type)
        return f"expiry:consumer:owner:{item_type}:{self.control_tag(shard_id)}"

    def bucket(self, ref: BucketRef) -> str:
        return (
            f"expiry:consumer:bucket:{self.service_key}:"
            f"{ref.item_type}:{ref.shard_id}:{ref.minute_ms}"
        )

    def registry_member(self, ref: BucketRef) -> str:
        return f"{ref.item_type}:{ref.shard_id}:{ref.minute_ms}"

    def parse_registry_member(self, value: str, *, expected_shard: int) -> BucketRef:
        parts = value.split(":")
        if len(parts) != 3:
            raise ExpiryError("invalid_identity", "Invalid expiry registry member")
        validate_item_type(parts[0])
        try:
            shard_id, minute_ms = int(parts[1]), int(parts[2])
        except ValueError as exc:
            raise ExpiryError("invalid_identity", "Invalid expiry registry member") from exc
        if shard_id != expected_shard or minute_ms < 0 or minute_ms % MINUTE_MS:
            raise ExpiryError("invalid_identity", "Invalid expiry registry member")
        return BucketRef(parts[0], shard_id, minute_ms)
