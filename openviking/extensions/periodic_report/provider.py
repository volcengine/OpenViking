# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenViking Resource provider for generated periodic reports.

The provider deliberately starts after a report has been selected and rendered.
It archives the same Markdown and HTML payload, verifies exact Resource
readback, and exposes a structured query surface for a future reports UI.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from hashlib import sha256
from typing import Any, Mapping, Protocol

from openviking_cli.exceptions import AlreadyExistsError, NotFoundError
from openviking_cli.utils import VikingURI

from .manifest import EXTENSION_MANIFEST

ARCHIVE_SCHEMA_VERSION = "openviking_periodic_report_archive_v0"
ACTIVATION_SCHEMA_VERSION = "periodic_report_activation_v0"
SINK_BINDING_SCHEMA_VERSION = "periodic_report_sink_binding_v0"
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_VERSION_TOKEN = re.compile(r"^[0-9A-Za-z][A-Za-z0-9._+-]{0,127}$")


class PeriodicReportArchiveError(RuntimeError):
    """Base error for periodic-report archive operations."""


class PeriodicReportArchiveConflict(PeriodicReportArchiveError):
    """The stable report identity already contains different content."""


class PeriodicReportArchiveIntegrityError(PeriodicReportArchiveError):
    """Stored archive content does not match its manifest or receipt."""


class PeriodicReportActivationError(PeriodicReportArchiveError):
    """The LoopX periodic-report capability is not active for this sink."""


class PeriodicReportArchiveClient(Protocol):
    """Small async OpenViking client surface required by the provider."""

    async def read(self, uri: str, offset: int = 0, limit: int = -1) -> str: ...

    async def write(
        self,
        uri: str,
        content: str,
        mode: str = "replace",
        wait: bool = False,
        timeout: float | None = None,
        telemetry: bool = False,
    ) -> dict[str, Any]: ...

    async def glob(self, pattern: str, uri: str = "viking://") -> dict[str, Any]: ...


@dataclass(frozen=True)
class PeriodicReportBundle:
    """A report that has already been selected and rendered upstream."""

    project_key: str
    profile_id: str
    profile_version: str
    report_id: str
    period_start: str
    period_end: str
    generated_at: str
    markdown: str
    html: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_segment("project_key", self.project_key)
        _validate_segment("profile_id", self.profile_id)
        _validate_version("profile_version", self.profile_version)
        _validate_segment("report_id", self.report_id)
        start = _parse_date("period_start", self.period_start)
        end = _parse_date("period_end", self.period_end)
        if end < start:
            raise ValueError("period_end must be on or after period_start")
        _parse_datetime("generated_at", self.generated_at)
        if not isinstance(self.markdown, str) or not self.markdown.strip():
            raise ValueError("markdown must be a non-empty string")
        if not isinstance(self.html, str) or not self.html.strip():
            raise ValueError("html must be a non-empty string")
        if not all(isinstance(key, str) for key in self.metadata):
            raise ValueError("metadata keys must be strings")
        try:
            normalized_metadata = json.loads(_canonical_json(dict(self.metadata)))
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must be JSON-serializable") from exc
        object.__setattr__(self, "metadata", normalized_metadata)


@dataclass(frozen=True)
class PeriodicReportArchiveReceipt:
    """Exact readback receipt for one archived report."""

    schema_version: str
    extension_id: str
    protocol_version: str
    result_id: str
    resource_uri: str
    manifest_uri: str
    markdown_uri: str
    html_uri: str
    bundle_digest: str
    write_status: str
    exact_readback_verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "extension_id": self.extension_id,
            "protocol_version": self.protocol_version,
            "result_id": self.result_id,
            "resource_uri": self.resource_uri,
            "manifest_uri": self.manifest_uri,
            "markdown_uri": self.markdown_uri,
            "html_uri": self.html_uri,
            "bundle_digest": self.bundle_digest,
            "write_status": self.write_status,
            "exact_readback_verified": self.exact_readback_verified,
        }


class PeriodicReportArchiveProvider:
    """Optional archive/query provider backed by OpenViking Resources."""

    def __init__(
        self,
        client: PeriodicReportArchiveClient,
        *,
        root_uri: str = "viking://resources/periodic-reports",
    ) -> None:
        normalized_root = root_uri.rstrip("/")
        if not normalized_root.startswith("viking://") or any(
            marker in normalized_root for marker in ("?", "#", "\\")
        ):
            raise ValueError("root_uri must be a safe viking:// URI")
        parsed_root = VikingURI(normalized_root)
        path_segments = parsed_root.full_path.split("/")[1:]
        if (
            parsed_root.scope != "resources"
            or not path_segments
            or any(not _SAFE_SEGMENT.fullmatch(segment) for segment in path_segments)
        ):
            raise ValueError("root_uri must use a safe project Resource path")
        self._client = client
        self._root_uri = normalized_root

    @property
    def root_uri(self) -> str:
        return self._root_uri

    def readiness(
        self,
        *,
        activation_receipt: Mapping[str, Any] | None = None,
        sink_id: str | None = None,
        bundle: PeriodicReportBundle | None = None,
    ) -> dict[str, Any]:
        """Describe provider readiness after capability/profile activation."""

        activation_error: str | None = None
        if activation_receipt is None or sink_id is None or bundle is None:
            activation_error = "periodic_report_activation_required"
        else:
            try:
                self._validate_activation(
                    activation_receipt,
                    bundle=bundle,
                    sink_id=sink_id,
                    sink_kind="project_resource",
                )
            except PeriodicReportActivationError as exc:
                activation_error = str(exc)
        ready = activation_error is None

        return {
            "extension_id": EXTENSION_MANIFEST["id"],
            "extension_version": EXTENSION_MANIFEST["version"],
            "protocol": EXTENSION_MANIFEST["protocol_version"],
            "status": "ready" if ready else "unavailable",
            "readback_verified": ready,
            "capabilities": sorted(
                [
                    {
                        "capability_id": capability_id,
                        "capability_version": capability["version"],
                    }
                    for capability_id, capability in EXTENSION_MANIFEST["capabilities"].items()
                ],
                key=lambda item: (item["capability_id"], item["capability_version"]),
            ),
            "activation_verified": ready,
            "activation_error": activation_error,
        }

    async def archive_sink(
        self,
        bundle: PeriodicReportBundle,
        *,
        sink_id: str,
        idempotency_key: str,
        activation_receipt: Mapping[str, Any],
        sink_kind: str = "project_resource",
    ) -> dict[str, Any]:
        """Archive a bundle and return a LoopX periodic-report sink result."""

        _validate_segment("sink_id", sink_id)
        _validate_segment("sink_kind", sink_kind)
        self._validate_activation(
            activation_receipt,
            bundle=bundle,
            sink_id=sink_id,
            sink_kind=sink_kind,
        )
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
            raise ValueError("idempotency_key must be a non-empty string up to 128 characters")
        receipt = await self.archive(bundle)
        return {
            "schema_version": "periodic_report_sink_result_v0",
            "sink_id": sink_id,
            "sink_kind": sink_kind,
            "sink_role": "archive",
            "status": "sent",
            "idempotency_key": idempotency_key,
            "receipt_ref": receipt.manifest_uri,
            "result_id": receipt.result_id,
            "readback_verified": receipt.exact_readback_verified,
            "retryable": False,
            "schedule_policy_applied": False,
            "business_evidence_judged": False,
            "write_status": receipt.write_status,
            "bundle_digest": receipt.bundle_digest,
            "capability_activation_verified": True,
        }

    async def archive(self, bundle: PeriodicReportBundle) -> PeriodicReportArchiveReceipt:
        """Archive a bundle and return a deterministic exact-readback receipt.

        The manifest is written last. A retry after a partial failure reuses
        byte-identical files, while any content mismatch fails closed.
        """

        manifest, payloads = self._build_archive(bundle)
        writes = 0
        for uri, content in payloads:
            writes += int(await self._ensure_exact_content(uri, content))

        manifest_uri = str(manifest["manifest_uri"])
        manifest_content = _canonical_json(manifest) + "\n"
        writes += int(await self._ensure_exact_content(manifest_uri, manifest_content))

        receipt = await self.readback(
            manifest_uri,
            expected_result_id=str(manifest["result_id"]),
            expected_bundle_digest=str(manifest["bundle_digest"]),
        )
        if writes == 0:
            status = "already_present"
        elif writes == len(payloads) + 1:
            status = "created"
        else:
            status = "recovered"
        return replace(receipt, write_status=status)

    async def readback(
        self,
        manifest_uri: str,
        *,
        expected_result_id: str | None = None,
        expected_bundle_digest: str | None = None,
    ) -> PeriodicReportArchiveReceipt:
        """Verify the manifest, result id, digests, and both report payloads."""

        raw_manifest = await self._client.read(manifest_uri)
        try:
            manifest = json.loads(raw_manifest)
        except json.JSONDecodeError as exc:
            raise PeriodicReportArchiveIntegrityError(
                f"invalid archive manifest JSON: {manifest_uri}"
            ) from exc
        if not isinstance(manifest, dict):
            raise PeriodicReportArchiveIntegrityError(
                f"archive manifest must be an object: {manifest_uri}"
            )
        self._verify_manifest(manifest, manifest_uri)

        stored_bundle_digest = _required_string(manifest, "bundle_digest", manifest_uri)
        stored_result_id = _required_string(manifest, "result_id", manifest_uri)
        core = dict(manifest)
        core.pop("bundle_digest", None)
        core.pop("result_id", None)
        calculated_bundle_digest = _digest(_canonical_json(core))
        calculated_result_id = _digest(f"{manifest_uri}\n{calculated_bundle_digest}")
        if stored_bundle_digest != calculated_bundle_digest:
            raise PeriodicReportArchiveIntegrityError(f"bundle digest mismatch for {manifest_uri}")
        if stored_result_id != calculated_result_id:
            raise PeriodicReportArchiveIntegrityError(f"result id mismatch for {manifest_uri}")
        if expected_bundle_digest and stored_bundle_digest != expected_bundle_digest:
            raise PeriodicReportArchiveIntegrityError(
                f"unexpected bundle digest for {manifest_uri}"
            )
        if expected_result_id and stored_result_id != expected_result_id:
            raise PeriodicReportArchiveIntegrityError(f"unexpected result id for {manifest_uri}")

        content = manifest.get("content")
        if not isinstance(content, dict):
            raise PeriodicReportArchiveIntegrityError(
                f"manifest content must be an object: {manifest_uri}"
            )
        resource_uri = _required_string(manifest, "resource_uri", manifest_uri)
        markdown_uri = await self._verify_content_entry(
            content,
            "markdown",
            manifest_uri,
            expected_uri=f"{resource_uri}/report.md",
            expected_media_type="text/markdown",
        )
        html_uri = await self._verify_content_entry(
            content,
            "html",
            manifest_uri,
            expected_uri=f"{resource_uri}/report.html.txt",
            expected_media_type="text/html",
        )

        return PeriodicReportArchiveReceipt(
            schema_version="openviking_periodic_report_delivery_receipt_v0",
            extension_id=str(EXTENSION_MANIFEST["id"]),
            protocol_version=str(EXTENSION_MANIFEST["protocol_version"]),
            result_id=stored_result_id,
            resource_uri=resource_uri,
            manifest_uri=manifest_uri,
            markdown_uri=markdown_uri,
            html_uri=html_uri,
            bundle_digest=stored_bundle_digest,
            write_status="readback",
            exact_readback_verified=True,
        )

    async def query(
        self,
        *,
        project_key: str,
        since: str | None = None,
        until: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return verified report manifests for a project, newest first."""

        _validate_segment("project_key", project_key)
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        since_date = _parse_date("since", since) if since else None
        until_date = _parse_date("until", until) if until else None
        if since_date and until_date and until_date < since_date:
            raise ValueError("until must be on or after since")

        project_root = f"{self._root_uri}/{project_key}"
        glob_result = await self._client.glob("**/manifest.json", uri=project_root)
        matches = glob_result.get("matches", []) if isinstance(glob_result, dict) else []
        if not isinstance(matches, list):
            raise PeriodicReportArchiveIntegrityError("glob matches must be a list")

        if not all(isinstance(match, str) for match in matches):
            raise PeriodicReportArchiveIntegrityError("glob manifest URI must be a string")

        records: list[dict[str, Any]] = []
        for manifest_uri in sorted(set(matches)):
            raw_manifest = await self._client.read(manifest_uri)
            try:
                manifest = json.loads(raw_manifest)
            except json.JSONDecodeError as exc:
                raise PeriodicReportArchiveIntegrityError(
                    f"invalid archive manifest JSON: {manifest_uri}"
                ) from exc
            if not isinstance(manifest, dict):
                raise PeriodicReportArchiveIntegrityError(
                    f"archive manifest must be an object: {manifest_uri}"
                )
            receipt = await self.readback(
                manifest_uri,
                expected_result_id=_required_string(manifest, "result_id", manifest_uri),
                expected_bundle_digest=_required_string(manifest, "bundle_digest", manifest_uri),
            )
            identity = manifest["report_identity"]
            period_start = _parse_date("period_start", identity["period_start"])
            period_end = _parse_date("period_end", identity["period_end"])
            if since_date and period_end < since_date:
                continue
            if until_date and period_start > until_date:
                continue
            records.append(
                {
                    "report_identity": identity,
                    "capability_activation": manifest["capability_activation"],
                    "generated_at": manifest["generated_at"],
                    "metadata": manifest["metadata"],
                    "content": manifest["content"],
                    "receipt": receipt.to_dict(),
                }
            )

        records.sort(
            key=lambda record: (
                record["report_identity"]["period_end"],
                record["generated_at"],
                record["report_identity"]["report_id"],
            ),
            reverse=True,
        )
        return records[:limit]

    def _build_archive(
        self, bundle: PeriodicReportBundle
    ) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        resource_uri = (
            f"{self._root_uri}/{bundle.project_key}/{bundle.period_start}/{bundle.report_id}"
        )
        markdown_uri = f"{resource_uri}/report.md"
        # OpenViking content/write intentionally creates a bounded text-extension
        # set. Keep the HTML bytes in a text resource and declare its media type
        # in the manifest instead of widening the core write API.
        html_uri = f"{resource_uri}/report.html.txt"
        manifest_uri = f"{resource_uri}/manifest.json"
        core: dict[str, Any] = {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "extension_id": EXTENSION_MANIFEST["id"],
            "protocol_version": EXTENSION_MANIFEST["protocol_version"],
            "resource_uri": resource_uri,
            "manifest_uri": manifest_uri,
            "report_identity": {
                "project_key": bundle.project_key,
                "report_id": bundle.report_id,
                "period_start": bundle.period_start,
                "period_end": bundle.period_end,
            },
            "capability_activation": {
                "capability_id": "periodic-report",
                "activation_schema": ACTIVATION_SCHEMA_VERSION,
                "profile_id": bundle.profile_id,
                "profile_version": bundle.profile_version,
            },
            "generated_at": bundle.generated_at,
            "metadata": dict(bundle.metadata),
            "content": {
                "markdown": {
                    "uri": markdown_uri,
                    "media_type": "text/markdown",
                    "sha256": _digest(bundle.markdown),
                },
                "html": {
                    "uri": html_uri,
                    "media_type": "text/html",
                    "storage_encoding": "utf-8",
                    "sha256": _digest(bundle.html),
                },
            },
        }
        bundle_digest = _digest(_canonical_json(core))
        core["bundle_digest"] = bundle_digest
        core["result_id"] = _digest(f"{manifest_uri}\n{bundle_digest}")
        return core, [(markdown_uri, bundle.markdown), (html_uri, bundle.html)]

    async def _ensure_exact_content(self, uri: str, expected: str) -> bool:
        existing = await self._read_optional(uri)
        if existing is not None:
            if existing != expected:
                raise PeriodicReportArchiveConflict(
                    f"stable report URI already contains different content: {uri}"
                )
            return False
        try:
            await self._client.write(uri, expected, mode="create", wait=False)
            return True
        except AlreadyExistsError:
            # A concurrent identical retry may win between read and create.
            raced = await self._client.read(uri)
            if raced != expected:
                raise PeriodicReportArchiveConflict(
                    f"concurrent report write produced different content: {uri}"
                )
            return False

    async def _read_optional(self, uri: str) -> str | None:
        try:
            return await self._client.read(uri)
        except (NotFoundError, FileNotFoundError):
            return None

    def _verify_manifest(self, manifest: dict[str, Any], manifest_uri: str) -> None:
        if manifest.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
            raise PeriodicReportArchiveIntegrityError(
                f"unsupported archive schema for {manifest_uri}"
            )
        if manifest.get("extension_id") != EXTENSION_MANIFEST["id"]:
            raise PeriodicReportArchiveIntegrityError(f"unexpected extension id for {manifest_uri}")
        if manifest.get("protocol_version") != EXTENSION_MANIFEST["protocol_version"]:
            raise PeriodicReportArchiveIntegrityError(
                f"unexpected protocol version for {manifest_uri}"
            )
        if manifest.get("manifest_uri") != manifest_uri:
            raise PeriodicReportArchiveIntegrityError(
                f"manifest URI is not self-consistent: {manifest_uri}"
            )
        resource_uri = _required_string(manifest, "resource_uri", manifest_uri)
        if manifest_uri != f"{resource_uri}/manifest.json":
            raise PeriodicReportArchiveIntegrityError(
                f"resource and manifest URIs are not structurally bound: {manifest_uri}"
            )
        identity = manifest.get("report_identity")
        if not isinstance(identity, dict):
            raise PeriodicReportArchiveIntegrityError(
                f"report identity must be an object: {manifest_uri}"
            )
        for key in ("project_key", "report_id"):
            value = _required_string(identity, key, manifest_uri)
            try:
                _validate_segment(key, value)
            except ValueError as exc:
                raise PeriodicReportArchiveIntegrityError(str(exc)) from exc
        period_start = _parse_manifest_date(identity, "period_start", manifest_uri)
        period_end = _parse_manifest_date(identity, "period_end", manifest_uri)
        if period_end < period_start:
            raise PeriodicReportArchiveIntegrityError(
                f"period_end precedes period_start in {manifest_uri}"
            )
        expected_resource_uri = (
            f"{self._root_uri}/{identity['project_key']}/{identity['period_start']}"
            f"/{identity['report_id']}"
        )
        if resource_uri != expected_resource_uri:
            raise PeriodicReportArchiveIntegrityError(
                f"report identity and Resource URI are not structurally bound: {manifest_uri}"
            )
        if not isinstance(manifest.get("metadata"), dict):
            raise PeriodicReportArchiveIntegrityError(
                f"manifest metadata must be an object: {manifest_uri}"
            )
        activation = manifest.get("capability_activation")
        if not isinstance(activation, dict):
            raise PeriodicReportArchiveIntegrityError(
                f"capability activation must be an object: {manifest_uri}"
            )
        if (
            activation.get("capability_id") != "periodic-report"
            or activation.get("activation_schema") != ACTIVATION_SCHEMA_VERSION
        ):
            raise PeriodicReportArchiveIntegrityError(
                f"unexpected capability activation in {manifest_uri}"
            )
        profile_id = _required_string(activation, "profile_id", manifest_uri)
        profile_version = _required_string(activation, "profile_version", manifest_uri)
        try:
            _validate_segment("profile_id", profile_id)
            _validate_version("profile_version", profile_version)
        except ValueError as exc:
            raise PeriodicReportArchiveIntegrityError(str(exc)) from exc
        generated_at = _required_string(manifest, "generated_at", manifest_uri)
        try:
            _parse_datetime("generated_at", generated_at)
        except ValueError as exc:
            raise PeriodicReportArchiveIntegrityError(str(exc)) from exc

    async def _verify_content_entry(
        self,
        content: dict[str, Any],
        key: str,
        manifest_uri: str,
        *,
        expected_uri: str,
        expected_media_type: str,
    ) -> str:
        entry = content.get(key)
        if not isinstance(entry, dict):
            raise PeriodicReportArchiveIntegrityError(
                f"missing {key} content entry in {manifest_uri}"
            )
        uri = _required_string(entry, "uri", manifest_uri)
        if uri != expected_uri:
            raise PeriodicReportArchiveIntegrityError(
                f"unexpected {key} Resource URI in {manifest_uri}"
            )
        if entry.get("media_type") != expected_media_type:
            raise PeriodicReportArchiveIntegrityError(
                f"unexpected {key} media type in {manifest_uri}"
            )
        if key == "html" and entry.get("storage_encoding") != "utf-8":
            raise PeriodicReportArchiveIntegrityError(
                f"unexpected html storage encoding in {manifest_uri}"
            )
        expected_digest = _required_string(entry, "sha256", manifest_uri)
        actual = await self._client.read(uri)
        if _digest(actual) != expected_digest:
            raise PeriodicReportArchiveIntegrityError(f"{key} digest mismatch for {uri}")
        return uri

    def _validate_activation(
        self,
        raw: Mapping[str, Any],
        *,
        bundle: PeriodicReportBundle,
        sink_id: str,
        sink_kind: str,
    ) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise PeriodicReportActivationError("activation receipt must be an object")
        activation = dict(raw)
        if activation.get("schema_version") != ACTIVATION_SCHEMA_VERSION:
            raise PeriodicReportActivationError(
                f"activation receipt must use {ACTIVATION_SCHEMA_VERSION}"
            )
        if not (
            activation.get("status") == "enabled"
            and activation.get("active") is True
            and activation.get("generation_allowed") is True
        ):
            raise PeriodicReportActivationError(
                "periodic-report capability profile must be enabled"
            )
        profile = activation.get("profile")
        if not isinstance(profile, Mapping) or profile.get("enabled") is not True:
            raise PeriodicReportActivationError(
                "activation receipt must contain an enabled profile"
            )
        if (
            profile.get("profile_id") != bundle.profile_id
            or profile.get("profile_version") != bundle.profile_version
        ):
            raise PeriodicReportActivationError(
                "report bundle and activation receipt use different profiles"
            )
        try:
            expected_digest = "sha256:" + _digest(_canonical_json(profile))
        except (TypeError, ValueError) as exc:
            raise PeriodicReportActivationError(
                "activation receipt profile must be JSON-serializable"
            ) from exc
        if activation.get("profile_digest") != expected_digest:
            raise PeriodicReportActivationError(
                "activation receipt profile digest does not match the profile"
            )
        bindings = profile.get("sink_bindings")
        if not isinstance(bindings, list):
            raise PeriodicReportActivationError("activation profile must contain sink bindings")
        for raw_binding in bindings:
            if not isinstance(raw_binding, Mapping):
                continue
            binding = dict(raw_binding)
            if binding.get("sink_id") != sink_id:
                continue
            capability = binding.get("capability")
            extension = binding.get("extension")
            valid = (
                binding.get("schema_version") == SINK_BINDING_SCHEMA_VERSION
                and binding.get("sink_kind") == sink_kind
                and binding.get("sink_role") == "archive"
                and binding.get("dependency_policy") in {"optional", "required"}
                and isinstance(capability, Mapping)
                and capability.get("capability_id") == "report.archive.write"
                and capability.get("capability_version") == "v0"
                and isinstance(extension, Mapping)
                and extension.get("extension_id") == EXTENSION_MANIFEST["id"]
                and extension.get("extension_version") == EXTENSION_MANIFEST["version"]
                and extension.get("protocol") == EXTENSION_MANIFEST["protocol_version"]
            )
            if valid:
                return binding
            raise PeriodicReportActivationError(
                "periodic-report archive sink binding is incompatible or disabled"
            )
        raise PeriodicReportActivationError(
            "periodic-report profile does not bind this archive sink"
        )


def _required_string(payload: Mapping[str, Any], key: str, source: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PeriodicReportArchiveIntegrityError(f"missing string {key} in {source}")
    return value


def _validate_segment(name: str, value: str) -> None:
    if not isinstance(value, str) or not _SAFE_SEGMENT.fullmatch(value):
        raise ValueError(f"{name} must match {_SAFE_SEGMENT.pattern}")


def _validate_version(name: str, value: str) -> None:
    if not isinstance(value, str) or not _VERSION_TOKEN.fullmatch(value):
        raise ValueError(f"{name} must match {_VERSION_TOKEN.pattern}")


def _parse_date(name: str, value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO date") from exc


def _parse_manifest_date(identity: Mapping[str, Any], key: str, source: str) -> date:
    value = _required_string(identity, key, source)
    try:
        return _parse_date(key, value)
    except ValueError as exc:
        raise PeriodicReportArchiveIntegrityError(str(exc)) from exc


def _parse_datetime(name: str, value: str) -> datetime:
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a UTC offset")
    return parsed


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
