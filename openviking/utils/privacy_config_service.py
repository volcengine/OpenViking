# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Privacy config storage and redaction helpers.

Phase-1 scope only covers skill content redaction/restoration.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Dict, Optional

from openviking.core.namespace import canonical_user_root
from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import VikingURI, get_logger

logger = get_logger(__name__)


class PrivacyConfigService:
    """Stores per-user privacy configs and restores redacted skill content on read."""

    _ASSIGNMENT_PATTERN = re.compile(
        r"^(?P<prefix>\s*(?:export\s+)?(?P<key>[A-Za-z][A-Za-z0-9_-]*)\s*[:=]\s*)"
        r"(?P<value>.+?)(?P<suffix>\s*(?:[,;])?\s*(?:#.*)?)$"
    )
    _SENSITIVE_FIELDS = {
        "api_key",
        "token",
        "ak",
        "sk",
        "access_key",
        "secret_key",
        "client_secret",
        "base_url",
    }
    _SENSITIVE_SUFFIXES = (
        "_api_key",
        "_token",
        "_access_key",
        "_secret_key",
        "_client_secret",
        "_base_url",
    )

    def __init__(self, viking_fs: Optional[VikingFS] = None):
        self._viking_fs = viking_fs

    def set_viking_fs(self, viking_fs: VikingFS) -> None:
        self._viking_fs = viking_fs

    def _ensure_initialized(self) -> VikingFS:
        if not self._viking_fs:
            raise RuntimeError("PrivacyConfigService requires VikingFS")
        return self._viking_fs

    @classmethod
    def is_skill_content_uri(cls, uri: str) -> bool:
        normalized = VikingURI.normalize(uri)
        return normalized.startswith("viking://agent/skills/") and normalized.endswith("/SKILL.md")

    @classmethod
    def _normalize_field_name(cls, raw_key: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", raw_key.lower()).strip("_")
        return re.sub(r"_+", "_", normalized)

    @classmethod
    def _is_sensitive_field(cls, field_name: str) -> bool:
        return field_name in cls._SENSITIVE_FIELDS or field_name.endswith(cls._SENSITIVE_SUFFIXES)

    @staticmethod
    def _placeholder(field_name: str) -> str:
        return f"{{{{OV_PRIVACY:{field_name}}}}}"

    @staticmethod
    def _looks_like_literal_secret(value: str) -> bool:
        stripped = value.strip()
        if not stripped:
            return False
        if stripped.startswith("$"):
            return False
        if stripped.startswith("${") and stripped.endswith("}"):
            return False
        if stripped.startswith("{{OV_PRIVACY:"):
            return False
        return True

    @staticmethod
    def _strip_wrapping_quotes(value: str) -> str:
        stripped = value.strip()
        if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
            return stripped[1:-1]
        return stripped

    @classmethod
    def _replace_value_with_placeholder(cls, original_value: str, field_name: str) -> str:
        stripped = original_value.strip()
        placeholder = cls._placeholder(field_name)
        if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
            replacement = f"{stripped[0]}{placeholder}{stripped[0]}"
        else:
            replacement = placeholder
        leading_ws = original_value[: len(original_value) - len(original_value.lstrip())]
        trailing_ws = original_value[len(original_value.rstrip()) :]
        return f"{leading_ws}{replacement}{trailing_ws}"

    @classmethod
    def _target_key(cls, target_uri: str) -> str:
        normalized = VikingURI.normalize(target_uri)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def describe_storage(self, category: str, target_uri: str, ctx: RequestContext) -> Dict[str, str]:
        base_uri = (
            f"{canonical_user_root(ctx)}/privacy_configs/{category}/{self._target_key(target_uri)}"
        )
        return {
            "base_uri": base_uri,
            "current_uri": f"{base_uri}/current.json",
            "history_uri": f"{base_uri}/history",
            "meta_uri": f"{base_uri}/.meta.json",
        }

    async def load_current_config(
        self, category: str, target_uri: str, ctx: RequestContext
    ) -> Optional[Dict]:
        storage = self.describe_storage(category, target_uri, ctx)
        return await self._read_json(storage["current_uri"], ctx)

    async def sanitize_skill_content(self, skill_uri: str, content: str, ctx: RequestContext) -> str:
        redacted_content, secrets = self._redact_skill_content(content)
        if not secrets:
            return content
        await self._persist_config(
            category="skill",
            target_uri=skill_uri,
            secrets=secrets,
            ctx=ctx,
        )
        return redacted_content

    async def restore_skill_content(self, skill_uri: str, content: str, ctx: RequestContext) -> str:
        config = await self.load_current_config("skill", skill_uri, ctx)
        if not config:
            return content

        restored = content
        secrets = config.get("secrets", {})
        if not isinstance(secrets, dict):
            return content

        for field_name, secret in secrets.items():
            if not isinstance(field_name, str) or not isinstance(secret, str):
                continue
            restored = restored.replace(self._placeholder(field_name), secret)
        return restored

    def _redact_skill_content(self, content: str) -> tuple[str, Dict[str, str]]:
        secrets: Dict[str, str] = {}
        redacted_lines = []

        for line in content.splitlines(keepends=True):
            match = self._ASSIGNMENT_PATTERN.match(line.rstrip("\n"))
            if not match:
                redacted_lines.append(line)
                continue

            field_name = self._normalize_field_name(match.group("key"))
            if not self._is_sensitive_field(field_name):
                redacted_lines.append(line)
                continue

            original_value = match.group("value")
            secret = self._strip_wrapping_quotes(original_value)
            if not self._looks_like_literal_secret(secret):
                redacted_lines.append(line)
                continue

            secrets[field_name] = secret
            replacement = self._replace_value_with_placeholder(original_value, field_name)
            new_line = (
                f"{match.group('prefix')}{replacement}{match.group('suffix')}"
                + ("\n" if line.endswith("\n") else "")
            )
            redacted_lines.append(new_line)

        return "".join(redacted_lines), secrets

    async def _persist_config(
        self,
        category: str,
        target_uri: str,
        secrets: Dict[str, str],
        ctx: RequestContext,
    ) -> None:
        viking_fs = self._ensure_initialized()
        storage = self.describe_storage(category, target_uri, ctx)
        current_meta = await self._read_json(storage["meta_uri"], ctx)
        current_version = int(current_meta.get("current_version", 0)) if current_meta else 0
        next_version = current_version + 1
        version_name = f"version_{next_version:03d}.json"
        timestamp = datetime.now(timezone.utc).isoformat()

        await viking_fs.mkdir(storage["base_uri"], exist_ok=True, ctx=ctx)
        await viking_fs.mkdir(storage["history_uri"], exist_ok=True, ctx=ctx)

        payload = {
            "category": category,
            "target_uri": VikingURI.normalize(target_uri),
            "target_key": self._target_key(target_uri),
            "version": next_version,
            "created_at": timestamp,
            "secrets": secrets,
        }
        meta_payload = {
            "category": category,
            "target_uri": VikingURI.normalize(target_uri),
            "target_key": self._target_key(target_uri),
            "current_version": next_version,
            "updated_at": timestamp,
            "secret_fields": sorted(secrets.keys()),
        }

        await viking_fs.write_file(
            storage["current_uri"],
            json.dumps(payload, ensure_ascii=True, indent=2),
            ctx=ctx,
        )
        await viking_fs.write_file(
            f"{storage['history_uri']}/{version_name}",
            json.dumps(payload, ensure_ascii=True, indent=2),
            ctx=ctx,
        )
        await viking_fs.write_file(
            storage["meta_uri"],
            json.dumps(meta_payload, ensure_ascii=True, indent=2),
            ctx=ctx,
        )

    async def _read_json(self, uri: str, ctx: RequestContext) -> Optional[Dict]:
        viking_fs = self._ensure_initialized()
        try:
            raw = await viking_fs.read_file(uri, ctx=ctx)
        except NotFoundError:
            return None
        except Exception as exc:
            logger.warning("Failed to read privacy config %s: %s", uri, exc)
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse privacy config %s", uri)
            return None
        return data if isinstance(data, dict) else None
