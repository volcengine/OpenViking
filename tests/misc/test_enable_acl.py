"""Offline checks for the ordering and safety of the ACL upgrade script."""

from __future__ import annotations

import copy
import sys
import types
from types import SimpleNamespace

import pytest
import requests

from scripts.maintenance import enable_acl as migration


class FakeConsole:
    def __init__(self, *, fields=None, scalar=None, status="READY", events=None):
        self.target = {"ProjectName": "project", "CollectionName": "context"}
        self.index = "default"
        self.fields = copy.deepcopy(fields if fields is not None else [])
        self.scalar = copy.deepcopy(scalar)
        self.status = status
        self.events = events if events is not None else []
        self.writes = []
        self.pending_reads = 0
        self.fail_action = None

    def collection_meta(self):
        return {**self.target, "Fields": copy.deepcopy(self.fields)}

    def index_meta(self):
        status = self.status
        if self.pending_reads:
            status = "UPDATING"
            self.pending_reads -= 1
        self.events.append(("index", status))
        return {
            **self.target,
            "IndexName": self.index,
            "Status": status,
            "ScalarIndex": copy.deepcopy(self.scalar),
        }

    def call(self, action, **payload):
        self.events.append((action, copy.deepcopy(payload)))
        if action == self.fail_action:
            raise RuntimeError("management API failed")
        self.writes.append((action, copy.deepcopy(payload)))
        if action == "UpdateVikingdbCollection":
            self.fields.extend(copy.deepcopy(payload["Fields"]))
        elif action == "UpdateVikingdbIndex":
            self.scalar = copy.deepcopy(payload["ScalarIndex"])
            self.pending_reads = 1
        else:
            raise AssertionError(f"Unexpected action: {action}")
        return {"Message": "success"}


class FakeAdmin(migration.Admin):
    def __init__(
        self, *, accounts=None, settings=None, events=None, vector=None, configurations=None
    ):
        self.accounts = (
            accounts if accounts is not None else [{"account_id": "existing", "status": "active"}]
        )
        self.settings = (
            copy.deepcopy(settings)
            if settings is not None
            else {"existing": {"acl": {"enabled": False}, "github": {"enabled": True}}}
        )
        self.events = events if events is not None else []
        self.calls = []
        self.vector = vector
        self.configurations = configurations or {}

    def call(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        self.events.append((method, path))
        if path == "/accounts":
            assert method == "GET"
            return copy.deepcopy(self.accounts)
        if path.endswith("/configuration"):
            assert method == "GET"
            account = next(
                account
                for account in self.settings
                if self.settings_path(account).removesuffix("settings") + "configuration" == path
            )
            return {"settings": copy.deepcopy(self.configurations.get(account, {}))}
        account = next(account for account in self.settings if self.settings_path(account) == path)
        if method == "PATCH":
            self.settings[account].update(copy.deepcopy(body))
        else:
            assert method == "GET"
        return {"settings": copy.deepcopy(self.settings[account])}

    @property
    def patches(self):
        return [call for call in self.calls if call[0] == "PATCH"]


def args(**changes):
    values = {
        "account": None,
        "execute": True,
        "phase": "all",
        "wait_timeout": 1,
        "poll_interval": 0.001,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_dry_run_reads_accounts_and_schema_without_writes():
    console = FakeConsole(scalar=["account_id"])
    admin = FakeAdmin()

    migration.run(console, admin, args(execute=False))

    assert console.writes == []
    assert admin.patches == []
    assert admin.calls == [
        ("GET", "/accounts", None),
        ("GET", "/accounts/existing/settings", None),
    ]


def test_adds_only_missing_fields_and_preserves_existing_scalar_indexes(monkeypatch):
    monkeypatch.setattr(migration.time, "sleep", lambda _: None)
    events = []
    console = FakeConsole(
        fields=[{"FieldName": "custom", "FieldType": "int64"}, migration.ACL_FIELDS[0]],
        scalar=[{"FieldName": "custom", "FieldType": "int64"}, "account_id", "acl_mode"],
        events=events,
    )
    admin = FakeAdmin(events=events)

    migration.run(console, admin, args())

    assert console.writes == [
        ("UpdateVikingdbCollection", {"Fields": migration.ACL_FIELDS[1:]}),
        (
            "UpdateVikingdbIndex",
            {
                "IndexName": "default",
                "ScalarIndex": ["custom", "account_id", *migration.ACL_NAMES],
            },
        ),
    ]
    assert admin.patches == [("PATCH", "/accounts/existing/settings", {"acl": {"enabled": True}})]
    assert admin.settings["existing"]["github"] == {"enabled": True}
    patch_position = events.index(("PATCH", "/accounts/existing/settings"))
    updating_position = events.index(("index", "UPDATING"))
    assert events[updating_position + 1 : patch_position] == [("index", "READY")]

    # A restart after completion must leave both schema and settings untouched.
    console.writes.clear()
    admin.calls.clear()
    migration.run(console, admin, args())
    assert console.writes == []
    assert admin.patches == []


def test_null_scalar_index_retains_all_fields_semantics():
    console = FakeConsole(fields=[], scalar=None)
    admin = FakeAdmin()

    migration.run(console, admin, args())

    assert console.writes == [("UpdateVikingdbCollection", {"Fields": migration.ACL_FIELDS})]
    assert console.scalar is None
    assert len(admin.patches) == 1


def test_accounts_phase_requires_complete_schema_without_mutating_it():
    console = FakeConsole(scalar=[])
    admin = FakeAdmin()

    with pytest.raises(RuntimeError, match="schema is incomplete"):
        migration.run(console, admin, args(phase="accounts"))

    assert console.writes == []
    assert admin.patches == []


@pytest.mark.parametrize("action", ["UpdateVikingdbCollection", "UpdateVikingdbIndex"])
def test_management_failure_never_enables_accounts(action):
    console = FakeConsole(scalar=[])
    console.fail_action = action
    admin = FakeAdmin()

    with pytest.raises(RuntimeError, match="management API failed"):
        migration.run(console, admin, args())

    assert admin.patches == []


@pytest.mark.parametrize("status", ["UPDATING", "FAILED", "DISABLED", "unknown"])
def test_unready_index_never_enables_accounts(status):
    console = FakeConsole(fields=migration.ACL_FIELDS, scalar=migration.ACL_NAMES, status=status)
    admin = FakeAdmin()

    with pytest.raises((RuntimeError, TimeoutError)):
        migration.run(console, admin, args(wait_timeout=0))

    assert console.writes == []
    assert admin.patches == []


def test_index_build_timeout_after_schema_update_never_enables_accounts():
    console = FakeConsole(fields=migration.ACL_FIELDS, scalar=[])
    admin = FakeAdmin()

    with pytest.raises(TimeoutError, match="ACL scalar indexes READY"):
        migration.run(console, admin, args(wait_timeout=0))

    assert console.writes[0][0] == "UpdateVikingdbIndex"
    assert admin.patches == []


@pytest.mark.parametrize(
    ("field_index", "key", "value"),
    [(0, "FieldType", "int64"), (0, "DefaultValue", "inherit"), (1, "DefaultValue", [])],
)
def test_incompatible_existing_fields_abort_before_writes(field_index, key, value):
    fields = copy.deepcopy(migration.ACL_FIELDS)
    fields[field_index][key] = value
    console = FakeConsole(fields=fields, scalar=migration.ACL_NAMES)
    admin = FakeAdmin()

    with pytest.raises(RuntimeError, match="Incompatible existing ACL field"):
        migration.run(console, admin, args())

    assert console.writes == []
    assert admin.patches == []


def test_all_accounts_skips_inactive_and_already_enabled_accounts():
    admin = FakeAdmin(
        accounts=[
            {"account_id": "active", "status": "active"},
            {"account_id": "legacy"},
            {"account_id": "disabled", "status": "disabled"},
            {"account_id": "active", "status": "active"},
        ],
        settings={
            "active": {"acl": {"enabled": False}},
            "legacy": {"acl": {"enabled": True}},
            "disabled": {"acl": {"enabled": False}},
        },
    )

    plan = admin.account_plan(None)
    admin.enable(plan, execute=True)

    assert plan == {"active": False, "legacy": True}
    assert admin.patches == [("PATCH", "/accounts/active/settings", {"acl": {"enabled": True}})]
    assert not any("disabled" in path for _, path, _ in admin.calls)


def test_explicit_accounts_are_deduplicated_and_path_quoted():
    admin = FakeAdmin(settings={"account/space": {"acl": {"enabled": False}}})

    plan = admin.account_plan(["account/space", "account/space"])
    admin.enable(plan, execute=True)

    assert admin.calls == [
        ("GET", "/accounts/account%2Fspace/settings", None),
        ("PATCH", "/accounts/account%2Fspace/settings", {"acl": {"enabled": True}}),
    ]


@pytest.mark.parametrize("acl", [{}, {"enabled": "false"}, {"enabled": 0}])
def test_preflight_rejects_servers_without_boolean_acl_setting(acl):
    console = FakeConsole(scalar=[])
    admin = FakeAdmin(settings={"existing": {"acl": acl}})

    with pytest.raises(RuntimeError, match="deploy an ACL-capable version"):
        migration.run(console, admin, args())

    assert console.writes == []
    assert admin.patches == []


def test_vector_target_honors_project_alias_and_defaults():
    assert migration.vector_target(
        {"backend": "volcengine", "project": "production", "project_name": "stale"}
    ) == ("volcengine", "production", "context", "default")
    assert migration.vector_target({"backend": "volcengine", "project_name": "legacy"}) == (
        "volcengine",
        "legacy",
        "context",
        "default",
    )


@pytest.mark.parametrize(
    "override",
    [
        {"name": "another_collection"},
        {"project_name": "another_project"},
        {"index_name": "another_index"},
        {"backend": "local"},
        {"volcengine": {"region": "cn-shanghai", "api_key": "secret-other-tenant"}},
    ],
)
def test_account_target_mismatch_aborts_all_accounts_before_any_write(override):
    vector = {
        "backend": "volcengine",
        "project": "production",
        "name": "context",
        "index_name": "default",
        "volcengine": {"region": "cn-beijing", "api_key": "secret-original-tenant"},
    }
    account_vector = {**vector, **override}
    if "project_name" in override:
        account_vector.pop("project")
    console = FakeConsole(scalar=[])
    admin = FakeAdmin(
        vector=vector,
        settings={"first": {"acl": {"enabled": False}}, "second": {"acl": {"enabled": False}}},
        configurations={"second": {"vectordb": account_vector}},
    )

    with pytest.raises(RuntimeError, match="different vector target/connection") as exc:
        migration.run(console, admin, args(account=["first", "second"]))

    assert "secret" not in str(exc.value)
    assert console.writes == []
    assert admin.patches == []


def test_account_target_accepts_inheritance_and_matching_project_name_alias():
    vector = {
        "backend": "volcengine",
        "project": "production",
        "volcengine": {"region": "cn-beijing", "api_key": "test-key"},
    }
    account_vector = {**vector, "project_name": "production"}
    account_vector.pop("project")
    console = FakeConsole(fields=migration.ACL_FIELDS, scalar=migration.ACL_NAMES)
    admin = FakeAdmin(
        vector=vector,
        settings={"first": {"acl": {"enabled": False}}, "second": {"acl": {"enabled": False}}},
        configurations={"second": {"vectordb": account_vector}},
    )

    migration.run(console, admin, args(account=["first", "second"]))

    assert console.writes == []
    assert len(admin.patches) == 2
    assert admin.calls[:4] == [
        ("GET", "/accounts/first/configuration", None),
        ("GET", "/accounts/first/settings", None),
        ("GET", "/accounts/second/configuration", None),
        ("GET", "/accounts/second/settings", None),
    ]


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("HTTP request failed")

    def json(self):
        return self.payload


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse({"status": "error", "result": {}}), RuntimeError),
        (FakeResponse({"status": "ok", "result": {}}, status_code=403), requests.HTTPError),
    ],
)
def test_admin_rejects_application_and_http_errors(response, error):
    admin = migration.Admin("https://ov.example", "test-key")
    calls = []

    def request(*positional, **keywords):
        calls.append((positional, keywords))
        return response

    admin.session.request = request
    with pytest.raises(error):
        admin.call("GET", "/accounts")
    assert calls[0][1]["allow_redirects"] is False


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse({"ResponseMetadata": {"Error": {"Code": "Denied"}}}), RuntimeError),
        (FakeResponse({"Result": []}), RuntimeError),
        (FakeResponse({"Result": {}}, status_code=403), requests.HTTPError),
    ],
)
def test_console_rejects_application_http_and_result_shape_errors(monkeypatch, response, error):
    # Avoid loading native OpenViking modules just to get the protocol version.
    module_name = "openviking.storage.vectordb.collection.volcengine_clients"
    stub = types.ModuleType(module_name)
    stub.VIKING_DB_VERSION = "2025-06-09"
    monkeypatch.setitem(sys.modules, module_name, stub)
    client = SimpleNamespace(do_req=lambda *_, **__: response)
    console = migration.Console(client, "project", "context", "default")

    with pytest.raises(error):
        console.call("UpdateVikingdbCollection", Fields=migration.ACL_FIELDS)
