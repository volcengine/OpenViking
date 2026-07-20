# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json
from fnmatch import fnmatch
from hashlib import sha256
from typing import Any

import pytest

from openviking.extensions.periodic_report import (
    PeriodicReportActivationError,
    PeriodicReportArchiveConflict,
    PeriodicReportArchiveIntegrityError,
    PeriodicReportArchiveProvider,
    PeriodicReportBundle,
    get_extension_manifest,
)
from openviking_cli.exceptions import AlreadyExistsError, NotFoundError


class FakeArchiveClient:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.write_calls: list[str] = []

    async def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        del offset, limit
        if uri not in self.files:
            raise NotFoundError(uri, "file")
        return self.files[uri]

    async def write(
        self,
        uri: str,
        content: str,
        mode: str = "replace",
        wait: bool = False,
        timeout: float | None = None,
        telemetry: bool = False,
    ) -> dict[str, Any]:
        del wait, timeout, telemetry
        if mode == "create" and uri in self.files:
            raise AlreadyExistsError(uri, "file")
        self.files[uri] = content
        self.write_calls.append(uri)
        return {"uri": uri}

    async def glob(self, pattern: str, uri: str = "viking://") -> dict[str, Any]:
        prefix = uri.rstrip("/") + "/"
        matches = [
            candidate
            for candidate in self.files
            if candidate.startswith(prefix) and fnmatch(candidate[len(prefix) :], pattern)
        ]
        return {"matches": sorted(matches), "count": len(matches)}


def make_bundle(
    *,
    report_id: str = "weekly-2026-07-20",
    period_start: str = "2026-07-13",
    period_end: str = "2026-07-19",
    markdown: str = "# Weekly report\n\n- shipped",
) -> PeriodicReportBundle:
    return PeriodicReportBundle(
        project_key="openviking",
        profile_id="openviking_weekly",
        profile_version="v1",
        report_id=report_id,
        period_start=period_start,
        period_end=period_end,
        generated_at=f"{period_end}T09:00:00+08:00",
        markdown=markdown,
        html=f"<html><body>{markdown}</body></html>",
        metadata={"trigger": "cadence", "renderer": "ov-weekly"},
    )


def make_activation(
    *,
    enabled: bool = True,
    dependency_policy: str = "optional",
    sink_id: str = "project_archive",
) -> dict[str, Any]:
    profile = {
        "schema_version": "periodic_report_profile_v0",
        "enabled": enabled,
        "profile_id": "openviking_weekly",
        "profile_version": "v1",
        "trigger_policy": {},
        "source_bindings": [],
        "renderer_bindings": [],
        "sink_bindings": [
            {
                "schema_version": "periodic_report_sink_binding_v0",
                "sink_id": sink_id,
                "sink_kind": "project_resource",
                "sink_role": "archive",
                "dependency_policy": dependency_policy,
                "capability": {
                    "capability_id": "report.archive.write",
                    "capability_version": "v0",
                },
                "extension": {
                    "extension_id": "openviking.periodic-report.archive",
                    "extension_version": "0.1.0",
                    "protocol": "periodic_report_sink_v0",
                },
            }
        ],
    }
    profile_digest = sha256(
        json.dumps(
            profile,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "periodic_report_activation_v0",
        "status": "enabled" if enabled else "disabled",
        "active": enabled,
        "generation_allowed": enabled,
        "profile_digest": f"sha256:{profile_digest}",
        "profile": profile,
    }


def test_manifest_is_default_off_and_provider_scoped() -> None:
    manifest = get_extension_manifest()

    assert manifest["default_enabled"] is False
    assert manifest["requires_capability"] == {
        "capability_id": "periodic-report",
        "activation_schema": "periodic_report_activation_v0",
        "profile_enabled": True,
        "sink_binding_schema": "periodic_report_sink_binding_v0",
        "accepted_dependency_policies": ["optional", "required"],
    }
    assert manifest["protocol_version"] == "periodic_report_sink_v0"
    assert set(manifest["capabilities"]) == {
        "report.archive.write",
        "report.archive.readback",
        "report.query",
    }
    assert manifest["capabilities"]["report.archive.write"] == {
        "version": "v0",
        "method": "archive_sink",
    }
    assert "lark_delivery" in manifest["boundary"]["does_not_own"]
    assert "trigger" in manifest["boundary"]["does_not_own"]


def test_readiness_matches_loopx_extension_binding() -> None:
    provider = PeriodicReportArchiveProvider(FakeArchiveClient())

    assert provider.readiness(
        activation_receipt=make_activation(),
        sink_id="project_archive",
        bundle=make_bundle(),
    ) == {
        "extension_id": "openviking.periodic-report.archive",
        "extension_version": "0.1.0",
        "protocol": "periodic_report_sink_v0",
        "status": "ready",
        "readback_verified": True,
        "capabilities": [
            {"capability_id": "report.archive.readback", "capability_version": "v0"},
            {"capability_id": "report.archive.write", "capability_version": "v0"},
            {"capability_id": "report.query", "capability_version": "v0"},
        ],
        "activation_verified": True,
        "activation_error": None,
    }


def test_readiness_is_unavailable_without_capability_activation() -> None:
    readiness = PeriodicReportArchiveProvider(FakeArchiveClient()).readiness()

    assert readiness["status"] == "unavailable"
    assert readiness["readback_verified"] is False
    assert readiness["activation_verified"] is False
    assert readiness["activation_error"] == "periodic_report_activation_required"


@pytest.mark.asyncio
async def test_archive_writes_bundle_and_exact_receipt() -> None:
    client = FakeArchiveClient()
    provider = PeriodicReportArchiveProvider(client)

    receipt = await provider.archive(make_bundle())

    assert receipt.write_status == "created"
    assert receipt.exact_readback_verified is True
    assert len(receipt.result_id) == 64
    assert client.files[receipt.markdown_uri].startswith("# Weekly report")
    assert client.files[receipt.html_uri].startswith("<html>")
    assert receipt.html_uri.endswith("report.html.txt")
    assert client.write_calls[-1] == receipt.manifest_uri


@pytest.mark.asyncio
async def test_archive_sink_returns_loopx_sink_result() -> None:
    provider = PeriodicReportArchiveProvider(FakeArchiveClient())

    result = await provider.archive_sink(
        make_bundle(),
        sink_id="project_archive",
        idempotency_key="report_sink_0123456789abcdef",
        activation_receipt=make_activation(),
    )

    assert result["schema_version"] == "periodic_report_sink_result_v0"
    assert result["sink_role"] == "archive"
    assert result["status"] == "sent"
    assert result["receipt_ref"].endswith("/manifest.json")
    assert result["readback_verified"] is True
    assert result["schedule_policy_applied"] is False
    assert result["business_evidence_judged"] is False
    assert result["capability_activation_verified"] is True


@pytest.mark.asyncio
async def test_archive_sink_rejects_disabled_or_missing_binding() -> None:
    provider = PeriodicReportArchiveProvider(FakeArchiveClient())

    with pytest.raises(PeriodicReportActivationError, match="must be enabled"):
        await provider.archive_sink(
            make_bundle(),
            sink_id="project_archive",
            idempotency_key="report_sink_disabled",
            activation_receipt=make_activation(enabled=False),
        )

    with pytest.raises(PeriodicReportActivationError, match="does not bind"):
        await provider.archive_sink(
            make_bundle(),
            sink_id="project_archive",
            idempotency_key="report_sink_missing",
            activation_receipt=make_activation(sink_id="different_archive"),
        )


@pytest.mark.asyncio
async def test_identical_retry_is_noop_with_same_result_id() -> None:
    client = FakeArchiveClient()
    provider = PeriodicReportArchiveProvider(client)
    bundle = make_bundle()

    first = await provider.archive(bundle)
    write_count = len(client.write_calls)
    second = await provider.archive(bundle)

    assert second.write_status == "already_present"
    assert second.result_id == first.result_id
    assert second.bundle_digest == first.bundle_digest
    assert len(client.write_calls) == write_count


@pytest.mark.asyncio
async def test_partial_retry_recovers_and_keeps_stable_identity() -> None:
    client = FakeArchiveClient()
    provider = PeriodicReportArchiveProvider(client)
    bundle = make_bundle()
    resource_uri = "viking://resources/periodic-reports/openviking/2026-07-13/weekly-2026-07-20"
    client.files[f"{resource_uri}/report.md"] = bundle.markdown

    receipt = await provider.archive(bundle)

    assert receipt.write_status == "recovered"
    assert receipt.exact_readback_verified is True
    assert f"{resource_uri}/report.md" not in client.write_calls
    assert client.write_calls[-1] == receipt.manifest_uri


@pytest.mark.asyncio
async def test_same_identity_with_different_content_fails_closed() -> None:
    client = FakeArchiveClient()
    provider = PeriodicReportArchiveProvider(client)
    await provider.archive(make_bundle())

    with pytest.raises(PeriodicReportArchiveConflict):
        await provider.archive(make_bundle(markdown="# changed"))


@pytest.mark.asyncio
async def test_readback_detects_tampered_payload() -> None:
    client = FakeArchiveClient()
    provider = PeriodicReportArchiveProvider(client)
    receipt = await provider.archive(make_bundle())
    client.files[receipt.html_uri] = "<html>tampered</html>"

    with pytest.raises(PeriodicReportArchiveIntegrityError, match="html digest mismatch"):
        await provider.readback(receipt.manifest_uri)


@pytest.mark.asyncio
async def test_query_returns_verified_newest_reports_and_filters_period() -> None:
    client = FakeArchiveClient()
    provider = PeriodicReportArchiveProvider(client)
    await provider.archive(make_bundle())
    await provider.archive(
        make_bundle(
            report_id="weekly-2026-07-27",
            period_start="2026-07-20",
            period_end="2026-07-26",
        )
    )

    all_records = await provider.query(project_key="openviking")
    assert [record["report_identity"]["report_id"] for record in all_records] == [
        "weekly-2026-07-27",
        "weekly-2026-07-20",
    ]

    records = await provider.query(
        project_key="openviking",
        since="2026-07-20",
        until="2026-07-31",
    )

    assert [record["report_identity"]["report_id"] for record in records] == ["weekly-2026-07-27"]
    assert records[0]["receipt"]["exact_readback_verified"] is True


def test_bundle_rejects_path_traversal_and_invalid_period() -> None:
    with pytest.raises(ValueError, match="project_key"):
        PeriodicReportBundle(
            project_key="../openviking",
            profile_id="openviking_weekly",
            profile_version="v1",
            report_id="weekly",
            period_start="2026-07-13",
            period_end="2026-07-19",
            generated_at="2026-07-20T09:00:00+08:00",
            markdown="# report",
            html="<h1>report</h1>",
        )

    with pytest.raises(ValueError, match="period_end"):
        make_bundle(period_start="2026-07-20", period_end="2026-07-19")


def test_bundle_detaches_metadata_from_mutable_input() -> None:
    metadata = {"trigger": "cadence", "labels": ["weekly"]}
    bundle = PeriodicReportBundle(
        project_key="openviking",
        profile_id="openviking_weekly",
        profile_version="v1",
        report_id="weekly-2026-07-20",
        period_start="2026-07-13",
        period_end="2026-07-19",
        generated_at="2026-07-19T09:00:00+08:00",
        markdown="# report",
        html="<h1>report</h1>",
        metadata=metadata,
    )

    metadata["trigger"] = "manual"
    metadata["labels"].append("changed")

    assert bundle.metadata == {"labels": ["weekly"], "trigger": "cadence"}


def test_provider_rejects_non_resource_roots() -> None:
    with pytest.raises(ValueError, match="Resource path"):
        PeriodicReportArchiveProvider(
            FakeArchiveClient(), root_uri="viking://user/memories/periodic-reports"
        )

    with pytest.raises(ValueError, match="Resource path"):
        PeriodicReportArchiveProvider(
            FakeArchiveClient(), root_uri="viking://resources/periodic-reports/../escape"
        )


def test_bundle_requires_offset_aware_generation_time() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        PeriodicReportBundle(
            project_key="openviking",
            profile_id="openviking_weekly",
            profile_version="v1",
            report_id="weekly-2026-07-20",
            period_start="2026-07-13",
            period_end="2026-07-19",
            generated_at="2026-07-19T09:00:00",
            markdown="# report",
            html="<h1>report</h1>",
        )


@pytest.mark.asyncio
async def test_readback_rejects_manifest_uri_rebinding() -> None:
    client = FakeArchiveClient()
    provider = PeriodicReportArchiveProvider(client)
    receipt = await provider.archive(make_bundle())
    manifest = json.loads(client.files[receipt.manifest_uri])
    manifest["resource_uri"] = "viking://resources/periodic-reports/other"
    client.files[receipt.manifest_uri] = json.dumps(manifest)

    with pytest.raises(PeriodicReportArchiveIntegrityError, match="structurally bound"):
        await provider.readback(receipt.manifest_uri)
