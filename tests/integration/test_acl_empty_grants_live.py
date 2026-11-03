# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Opt-in ACL sentinel contract test using an existing Volcengine collection.

Set OPENVIKING_ACL_LIVE_CONFIG to an ov.conf path. The test uses fixed vectors,
creates only unique account/URI/UUID records, and deletes its own records in finally.
It does not create collections or call embedding/VLM services.
"""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import ACL_CONTEXT_FIELDS, AclManager, AclMode
from openviking.storage.expr import PathScope
from openviking.storage.vectordb.collection.volcengine_clients import ClientForDataApiWithApiKey
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("OPENVIKING_ACL_LIVE_CONFIG"),
        reason="OPENVIKING_ACL_LIVE_CONFIG is not set",
    ),
]


class _AccountSettings:
    def __init__(self, config):
        self.settings = SimpleNamespace(vectordb=config, dedicated_vectordb=False)

    async def resolve(self, account_id):
        return self.settings

    async def get_account(self, account_id, section):
        assert section == "acl"
        return SimpleNamespace(enabled=True)


def _acl_fields(row):
    return {
        field: sorted(row[field]) if isinstance(row.get(field), list) else row.get(field)
        for field in ACL_CONTEXT_FIELDS
    }


@pytest.mark.asyncio
async def test_empty_acl_grants_roundtrip_and_revoke_on_volcengine(monkeypatch, tmp_path):
    raw = json.loads(Path(os.environ["OPENVIKING_ACL_LIVE_CONFIG"]).read_text())
    vector_config = dict(raw["storage"]["vectordb"])
    vector_config.setdefault(
        "dimension", raw.get("embedding", {}).get("dense", {}).get("dimension")
    )
    config = VectorDBBackendConfig(**vector_config)
    volcengine = config.volcengine
    if (
        config.backend != "volcengine"
        or config.dimension <= 0
        or volcengine is None
        or not volcengine.api_key
    ):
        pytest.fail("This test requires Volcengine API-key storage and a positive vector dimension")
    api_key = volcengine.api_key
    backend = VikingVectorIndexBackend(config)
    settings = _AccountSettings(config)
    backend.set_vector_config_resolver(settings)
    manager = backend.acl_manager = AclManager(backend, settings)
    run_id = uuid.uuid4().hex
    account = f"acl_empty_live_{run_id}"
    root = f"viking://resources/acl-empty-live-{run_id}"
    inherited_uri, private_uri = f"{root}/inherited", f"{root}/private"
    admin = RequestContext(UserIdentifier(account, "admin"), Role(Role.ADMIN))
    reader = RequestContext(UserIdentifier(account, "reader"), Role(Role.USER))
    member = RequestContext(
        UserIdentifier(account, "member"), Role(Role.USER), group_ids=("readers",)
    )
    direct_reader = RequestContext(UserIdentifier(account, "direct_reader"), Role(Role.USER))
    grants = ["1:group:readers", "1:user:reader"]
    direct_grants = ["1:user:direct_reader"]
    vector = [1.0] + [0.0] * (config.dimension - 1)
    keys = (
        "root0",
        "root1",
        "inherit0",
        "inherit1",
        "inherit_leaf",
        "private0",
        "private1",
        "private_leaf",
    )
    ids = {key: str(uuid.uuid4()) for key in keys}
    key_by_id = {value: key for key, value in ids.items()}
    all_ids = list(ids.values())
    inherited_keys = {"inherit0", "inherit1", "inherit_leaf"}
    private_keys = {"private0", "private1", "private_leaf"}
    evidence: dict[str, Any] = {
        "run_id": run_id,
        "account": account,
        "ids": ids,
        "requests": [],
        "stages": {},
    }
    evidence_path = tmp_path / "acl-empty-live-evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2))
    print(f"ACL_EMPTY_LIVE_EVIDENCE_PATH={evidence_path}", flush=True)
    phase = "setup"
    attempted_ids = []
    original_request = ClientForDataApiWithApiKey.do_req

    def capture(client, method, path, req_params=None, req_body=None):
        rows = (req_body or {}).get("data", [])
        write = path.endswith(("/upsert", "/update", "/delete"))
        request = {"phase": phase, "path": path}
        if write:
            if rows:
                assert all(row["id"] in key_by_id for row in rows)
                assert all(ACL_CONTEXT_FIELDS <= row.keys() for row in rows)
                request["acl"] = {key_by_id[row["id"]]: _acl_fields(row) for row in rows}
            else:
                assert set((req_body or {}).get("ids", [])) <= set(all_ids)
            evidence["requests"].append(request)
        try:
            response = original_request(
                client, method, path, req_params=req_params, req_body=req_body
            )
        except Exception as exc:
            if write:
                request["error_type"] = type(exc).__name__
            raise
        body = response.json()
        status = {"status": response.status_code, "code": body.get("code")}
        if write:
            request.update(status)
            print("ACL_EMPTY_LIVE_WRITE=" + json.dumps(request, sort_keys=True), flush=True)
        if status != {"status": 200, "code": "Success"}:
            message = str(body.get("message", "")).replace(api_key, "<redacted>")
            failure = {"phase": phase, "path": path, **status, "message": message}
            evidence.setdefault("failed_requests", []).append(failure)
            print("ACL_EMPTY_LIVE_REQUEST_ERROR=" + json.dumps(failure, sort_keys=True), flush=True)
        assert status == {"status": 200, "code": "Success"}, (
            f"Cloud request failed: path={path}, status={status}"
        )
        return response

    monkeypatch.setattr(ClientForDataApiWithApiKey, "do_req", capture)

    def record(key, uri, level, mode, direct, inherited):
        return {
            "id": ids[key],
            "account_id": account,
            "uri": uri,
            "context_type": "resource",
            "level": level,
            "name": f"acl-empty-live-{key}",
            "abstract": "Preserve this abstract across ACL changes.",
            "content": "Preserve this content across ACL changes.",
            "search_tags": ["suite=acl_empty_live"],
            "vector": vector,
            "created_at": "2026-11-03T00:00:00Z",
            "updated_at": "2026-11-03T00:00:00Z",
            "acl_mode": mode,
            "acl_direct_grants": direct,
            "acl_inherited_grants": inherited,
        }

    initial = [
        record("root0", root, 0, "restricted", grants, ["7:user:*"]),
        record("root1", root, 1, "restricted", grants, ["7:user:*"]),
        record("inherit0", inherited_uri, 0, "inherit", direct_grants, grants),
        record("inherit1", inherited_uri, 1, "inherit", direct_grants, grants),
        record(
            "inherit_leaf", f"{inherited_uri}/leaf.md", 2, "inherit", [], grants + direct_grants
        ),
        record("private0", private_uri, 0, "restricted", grants, grants),
        record("private1", private_uri, 1, "restricted", grants, grants),
        record("private_leaf", f"{private_uri}/leaf.md", 2, "inherit", [], grants),
    ]
    expected = {
        key_by_id[row["id"]]: {
            field: (["none"] if row[field] == [] else row[field]) for field in ACL_CONTEXT_FIELDS
        }
        for row in initial
    }

    async def fetch():
        # Unscoped fetch is also used to detect collisions before any writes.
        return await backend._shared_async_adapter.call("get", all_ids)

    async def eventually(label, check):
        started = time.monotonic()
        stage: dict[str, Any] = {"status": "running"}
        evidence["stages"][label] = stage
        print(f"ACL_EMPTY_LIVE_STAGE start {label}", flush=True)

        async def poll():
            while not await check():
                await asyncio.sleep(1)

        try:
            await asyncio.wait_for(poll(), timeout=60)
        except Exception as exc:
            stage.update(status="failed", error_type=type(exc).__name__)
            raise AssertionError(f"Live ACL stage failed: {label}") from exc
        else:
            stage["status"] = "passed"
        finally:
            stage["seconds"] = round(time.monotonic() - started, 3)
            print(f"ACL_EMPTY_LIVE_STAGE {label} {json.dumps(stage)}", flush=True)

    async def snapshot_matches():
        fetched = await fetch()
        fetched_acl = {key_by_id[row["id"]]: _acl_fields(row) for row in fetched}
        evidence["last_snapshot"] = {"fetched": fetched_acl}
        missing = {
            key_by_id[row["id"]]: sorted(ACL_CONTEXT_FIELDS - row.keys())
            for row in fetched
            if not ACL_CONTEXT_FIELDS <= row.keys()
        }
        assert not missing, (
            f"Fetch omitted ACL fields after successful upsert: {missing}. "
            "Check the existing collection schema; this test does not modify it."
        )
        indexed, cursor = await backend.scroll(
            filter=PathScope("uri", root, depth=-1),
            limit=32,
            output_fields=["id", *ACL_CONTEXT_FIELDS],
            ctx=admin,
        )
        observed = [fetched_acl, {key_by_id[row["id"]]: _acl_fields(row) for row in indexed}]
        evidence["last_snapshot"] = {"fetched": observed[0], "indexed": observed[1]}
        wanted = {key: _acl_fields(fields) for key, fields in expected.items()}
        return cursor is None and observed == [wanted, wanted]

    async def wait_visible(label, ctx, wanted):
        async def matches():
            random = await backend.search_by_random(
                filter=PathScope("uri", root, depth=-1), output_fields=["id"], limit=32, ctx=ctx
            )
            dense = await backend.search_in_tenant(
                ctx, vector, target_directories=[root], context_type="resource", limit=32
            )
            observed = [sorted(key_by_id[row["id"]] for row in rows) for rows in (random, dense)]
            evidence["stages"][label]["visibility"] = {"random": observed[0], "dense": observed[1]}
            return observed == [sorted(wanted), sorted(wanted)]

        await eventually(label, matches)

    async def clear(uri, mode, label):
        nonlocal phase
        phase = label
        before = len(evidence["requests"])
        await manager.set_acl(uri, [], admin, acl_mode=mode)
        writes = evidence["requests"][before:]
        assert writes and all(item["path"].endswith("/upsert") for item in writes)

    try:
        assert await fetch() == [], "UUID collision detected; no test records were written"
        attempted_ids = all_ids
        await backend._upsert_many_raw(initial, ctx=admin)
        await eventually("initial_fetch_and_index", snapshot_matches)
        before = {row["id"]: row for row in await fetch()}
        assert all(row["vector"] == vector for row in before.values())
        assert all(row["content"] == initial[0]["content"] for row in before.values())
        await wait_visible("initial_user_grants", reader, keys)
        await wait_visible("initial_group_grants", member, keys)
        await wait_visible("initial_direct_grants", direct_reader, inherited_keys)

        await clear(inherited_uri, AclMode.INHERIT, "clear_inherited_direct")
        for key in inherited_keys:
            expected[key]["acl_direct_grants"] = ["none"]
            expected[key]["acl_inherited_grants"] = grants
        await eventually("inherit_clear_fetch_and_index", snapshot_matches)
        await wait_visible("inherit_retains_user", reader, keys)
        await wait_visible("inherit_retains_group", member, keys)
        await wait_visible("inherit_revokes_direct_user", direct_reader, ())

        await clear(root, AclMode.RESTRICTED, "clear_restricted_root")
        for key in ("root0", "root1"):
            expected[key]["acl_direct_grants"] = ["none"]
        for key in (*inherited_keys, "private0", "private1"):
            expected[key]["acl_inherited_grants"] = ["none"]
        await eventually("root_clear_fetch_and_index", snapshot_matches)
        await wait_visible("restricted_boundary_user", reader, private_keys)
        await wait_visible("restricted_boundary_group", member, private_keys)

        await clear(private_uri, AclMode.RESTRICTED, "clear_restricted_private")
        for key in private_keys:
            expected[key]["acl_direct_grants"] = ["none"]
            expected[key]["acl_inherited_grants"] = ["none"]
        await eventually("all_clear_fetch_and_index", snapshot_matches)
        await wait_visible("clear_revokes_user", reader, ())
        await wait_visible("clear_revokes_group", member, ())

        after = {row["id"]: row for row in await fetch()}
        phase = "ordinary_upsert_after_clear"
        assert await backend.upsert(after[ids["private_leaf"]], ctx=admin) == ids["private_leaf"]
        await eventually("ordinary_upsert_retains_clear", snapshot_matches)
        await wait_visible("ordinary_upsert_user_still_denied", reader, ())
        await wait_visible("ordinary_upsert_group_still_denied", member, ())
        final = {row["id"]: row for row in await fetch()}
        for record_id, baseline in before.items():
            assert {k: v for k, v in final[record_id].items() if k not in ACL_CONTEXT_FIELDS} == {
                k: v for k, v in baseline.items() if k not in ACL_CONTEXT_FIELDS
            }
        evidence["result"] = "passed"
    except BaseException as exc:
        evidence["result"] = "failed"
        evidence["error_type"] = type(exc).__name__
        raise
    finally:
        try:
            if attempted_ids:
                phase = "cleanup"
                await backend._shared_async_adapter.call("delete", ids=attempted_ids)

                async def removed():
                    remaining = await fetch()
                    evidence["cleanup_remaining"] = [row["id"] for row in remaining]
                    return not remaining

                await eventually("cleanup_verified", removed)
        except BaseException as exc:
            evidence["result"] = "failed"
            evidence["cleanup_error_type"] = type(exc).__name__
            raise
        finally:
            await backend.close()
            evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True))
            print(f"ACL_EMPTY_LIVE_RESULT={evidence.get('result', 'failed')}", flush=True)
