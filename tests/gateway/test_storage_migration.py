"""Upgrade real v5 SQLite layouts, re-encrypt their values and keep tenant boundaries."""

import asyncio
import sqlite3
import time

import orjson
import pytest
from conftest import local_encryptor
from cryptography.fernet import Fernet, InvalidToken

from openviking.crypto.encryptor import MAGIC
from openviking_gateway.storage import (
    SQLiteKernelStore,
    SQLiteManagementStore,
    delete_account_data,
    delete_user_data,
    digest,
)

UPSTREAMS = [
    ("openai", "chat", "https://api.openai.com", "https://api.openai.com/v1"),
    ("ark", "chat", "https://ark.test", "https://ark.test/api/v3"),
    ("byteplus", "responses", "https://byteplus.test", "https://byteplus.test/api/v3"),
    ("ark", "anthropic", "https://ark.test", "https://ark.test/api/compatible"),
    ("byteplus", "anthropic", "https://byteplus.test", "https://byteplus.test/api/compatible"),
    ("anthropic", "anthropic", "https://anthropic.test/v1", "https://anthropic.test"),
    ("ark", "anthropic", "https://ark.test/api/compatible/v1", "https://ark.test/api/compatible"),
    # v5 requested base + /v1/...; this GLM base was already wrong and stays so.
    ("generic", "chat", "https://glm.test/api/paas/v4", "https://glm.test/api/paas/v4"),
    ("generic", "chat", "https://openrouter.ai/api", "https://openrouter.ai/api/v1"),
    ("generic", "responses", "https://api.groq.com/openai", "https://api.groq.com/openai/v1"),
    ("openai", "chat", "https://proxy.test/v1", "https://proxy.test/v1"),
    ("ark", "chat", "https://ark.test/custom", "https://ark.test/custom"),
    ("generic", "anthropic", "https://anthropic.test", "https://anthropic.test"),
]
# The v0.5.0 layouts, every value Fernet-encrypted with the gateway's own key.
V5_MANAGEMENT = """
    PRAGMA user_version=5;
    CREATE TABLE objects (account TEXT,kind TEXT,id TEXT,revision INTEGER,value BLOB,
        PRIMARY KEY(account,kind,id));
    CREATE INDEX object_identity ON objects(kind,id);
    CREATE TABLE request_logs (id INTEGER PRIMARY KEY,account TEXT,time REAL,value BLOB);
    CREATE INDEX log_account ON request_logs(account,time);
    CREATE TABLE object_expiry (account TEXT,kind TEXT,id TEXT,expires REAL,
        PRIMARY KEY(account,kind,id));
"""
V5_KERNEL = """
    PRAGMA user_version=5;
    CREATE TABLE sessions (scope TEXT,session TEXT,touched REAL NOT NULL,
        PRIMARY KEY(scope,session));
    CREATE TABLE replay (scope TEXT,session TEXT,kind TEXT,anchor TEXT,value BLOB NOT NULL,
        PRIMARY KEY(scope,session,kind,anchor),
        FOREIGN KEY(scope,session) REFERENCES sessions ON DELETE CASCADE);
    CREATE TABLE state (scope TEXT,key TEXT,value BLOB NOT NULL,version INTEGER NOT NULL,
        touched REAL NOT NULL, PRIMARY KEY(scope,key));
    CREATE TABLE capture (scope TEXT,session TEXT,value BLOB NOT NULL,version INTEGER NOT NULL,
        ready REAL,lease REAL NOT NULL DEFAULT 0,owner TEXT,PRIMARY KEY(scope,session),
        FOREIGN KEY(scope,session) REFERENCES sessions ON DELETE CASCADE);
    CREATE TABLE deleted_scopes (scope TEXT PRIMARY KEY);
"""
ENCRYPTED_TABLES = {
    "management.sqlite3": ("objects", "request_logs"),
    "kernel.sqlite3": ("replay", "state", "capture"),
}


@pytest.fixture(autouse=True)
def no_legacy_key_environment(monkeypatch):
    monkeypatch.delenv("OPENVIKING_GATEWAY_ENCRYPTION_KEY", raising=False)


@pytest.fixture
def v5_storage(tmp_path):
    """A v0.5.0 storage directory whose key sits in encryption.key."""
    key = Fernet.generate_key()
    (tmp_path / "encryption.key").write_bytes(key + b"\n")
    cipher = Fernet(key)

    def seal(value):
        return cipher.encrypt(orjson.dumps(value))

    with sqlite3.connect(tmp_path / "management.sqlite3") as c:
        c.executescript(V5_MANAGEMENT)
        for index, (vendor, protocol, base, _) in enumerate(UPSTREAMS):
            value = {"vendor": vendor, "protocol": protocol, "base_url": base}
            c.execute(
                "INSERT INTO objects VALUES ('a','upstreams',?,7,?)",
                (str(index), seal({**value, "api_key": "kept-secret"})),
            )
        c.execute(
            "INSERT INTO objects VALUES ('a','keys',?,1,?)",
            (digest("gateway-key"), seal({"user_id": "alice", "openviking_key": "ov-secret"})),
        )
        c.execute(
            "INSERT INTO request_logs(account,time,value) VALUES ('a',?,?)",
            (time.time(), seal({"request_id": "old"})),
        )
    with sqlite3.connect(tmp_path / "kernel.sqlite3") as c:
        c.executescript(V5_KERNEL)
        c.execute("INSERT INTO sessions VALUES ('scope','session',1000)")
        c.execute(
            "INSERT INTO replay VALUES ('scope','session','injection','anchor',?)",
            (seal({"text": "private memory"}),),
        )
        c.execute("INSERT INTO state VALUES ('scope','key',?,3,1000)", (seal({"budget": 9}),))
        c.execute(
            "INSERT INTO capture VALUES ('scope','session',?,4,NULL,0,NULL)",
            (seal({"pending": ["turn"]}),),
        )
    return tmp_path, key


def raw_values(directory):
    values = []
    for name, tables in ENCRYPTED_TABLES.items():
        with sqlite3.connect(directory / name) as c:
            for table in tables:
                values += [row[0] for row in c.execute(f"SELECT value FROM {table}")]
    return values


def user_version(path):
    with sqlite3.connect(path) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def migrated(values):
    return {v["id"]: (v["base_url"], v["revision"]) for v in values}


EXPECTED = {str(i): (r[3], 7 + (r[2] != r[3])) for i, r in enumerate(UPSTREAMS)}


@pytest.mark.parametrize("encrypted", [True, False], ids=["ov-encryption", "plaintext"])
async def test_v5_migration_reencodes_every_table(v5_storage, encrypted):
    directory, _ = v5_storage
    encryptor = local_encryptor(directory / "master.key") if encrypted else None
    management = SQLiteManagementStore(directory / "management.sqlite3", encryptor)
    kernel = SQLiteKernelStore(directory / "kernel.sqlite3", encryptor)
    try:
        await management.initialize()
        await kernel.initialize()
        values = await management.list("a", "upstreams")
        assert migrated(values) == EXPECTED
        assert all(v["api_key"] == "kept-secret" for v in values)
        assert (await management.authenticate("gateway-key"))["openviking_key"] == "ov-secret"
        assert [log["request_id"] for log in await management.logs("a")] == ["old"]
        assert await kernel.replay.read("scope", "session", ["anchor"]) == {
            ("injection", "anchor"): {"text": "private memory"}
        }
        state = (await kernel.state.read("scope", ["key"]))["key"]
        assert (state.value, state.version) == ({"budget": 9}, 3)
        capture = await kernel.capture.get("scope", "session")
        assert (capture.value, capture.version) == ({"pending": ["turn"]}, 4)
        # A second start finds v6 and leaves the data alone.
        await management.initialize()
        await kernel.initialize()
        assert await management.list("a", "upstreams") == values
    finally:
        management.close()
        kernel.close()
    raw = raw_values(directory)
    # Upstreams, the gateway key and the log, then replay, state and capture.
    assert len(raw) == len(UPSTREAMS) + 2 + 3
    if encrypted:
        assert all(value.startswith(MAGIC) for value in raw)
    else:
        assert [orjson.loads(value) for value in raw]
    assert user_version(directory / "management.sqlite3") == 6
    with sqlite3.connect(directory / "management.sqlite3") as c:
        columns = [r[2] for r in c.execute("PRAGMA index_info(log_account_user)")]
    assert columns == ["account", "user", "time"]


async def test_legacy_key_from_environment(v5_storage, monkeypatch):
    directory, key = v5_storage
    (directory / "encryption.key").unlink()
    monkeypatch.setenv("OPENVIKING_GATEWAY_ENCRYPTION_KEY", key.decode())
    kernel = SQLiteKernelStore(directory / "kernel.sqlite3")
    try:
        await kernel.initialize()
        assert (await kernel.state.read("scope", ["key"]))["key"].value == {"budget": 9}
    finally:
        kernel.close()


@pytest.mark.parametrize("name", list(ENCRYPTED_TABLES))
async def test_missing_legacy_key_refuses_startup(v5_storage, name):
    directory, _ = v5_storage
    (directory / "encryption.key").unlink()
    store = (SQLiteManagementStore if name == "management.sqlite3" else SQLiteKernelStore)(
        directory / name
    )
    try:
        with pytest.raises(ValueError, match="OPENVIKING_GATEWAY_ENCRYPTION_KEY") as error:
            await store.initialize()
        assert "encryption.key" in str(error.value)
    finally:
        store.close()
    assert user_version(directory / name) == 5


async def test_empty_v5_stores_need_no_legacy_key(tmp_path):
    for name, layout in (("management.sqlite3", V5_MANAGEMENT), ("kernel.sqlite3", V5_KERNEL)):
        with sqlite3.connect(tmp_path / name) as c:
            c.executescript(layout)
    management = SQLiteManagementStore(tmp_path / "management.sqlite3")
    kernel = SQLiteKernelStore(tmp_path / "kernel.sqlite3")
    try:
        await management.initialize()
        await kernel.initialize()
    finally:
        management.close()
        kernel.close()
    assert user_version(tmp_path / "kernel.sqlite3") == 6


async def test_failed_migration_rolls_back_schema_and_objects(v5_storage):
    directory, key = v5_storage
    path = directory / "management.sqlite3"
    with sqlite3.connect(path) as c:
        c.execute("UPDATE objects SET value=? WHERE id='8'", (b"corrupt",))
    store = SQLiteManagementStore(path)
    try:
        with pytest.raises(InvalidToken):
            await store.initialize()
    finally:
        store.close()
    assert user_version(path) == 5
    with sqlite3.connect(path) as c:
        assert "user" not in [r[1] for r in c.execute("PRAGMA table_info(request_logs)")]
        value, revision = c.execute("SELECT value,revision FROM objects WHERE id='0'").fetchone()
    assert orjson.loads(Fernet(key).decrypt(value))["base_url"] == "https://api.openai.com"
    assert revision == 7


async def test_workers_can_migrate_same_v5_database(v5_storage):
    directory, _ = v5_storage
    encryptor = local_encryptor(directory / "master.key")
    workers = [SQLiteManagementStore(directory / "management.sqlite3", encryptor) for _ in range(6)]
    try:
        await asyncio.gather(*(worker.initialize() for worker in workers))
        for worker in workers:
            assert migrated(await worker.list("a", "upstreams")) == EXPECTED
    finally:
        for worker in workers:
            worker.close()


async def test_enabling_encryption_later_keeps_plaintext_readable(tmp_path):
    path = tmp_path / "management.sqlite3"
    plain = SQLiteManagementStore(path)
    try:
        await plain.initialize()
        await plain.save("a", "upstreams", "old", {"name": "old"})
    finally:
        plain.close()
    encrypted = SQLiteManagementStore(path, local_encryptor(tmp_path / "master.key"))
    try:
        await encrypted.initialize()
        await encrypted.save("a", "upstreams", "new", {"name": "new"})
        assert [v["name"] for v in await encrypted.list("a", "upstreams")] == ["new", "old"]
    finally:
        encrypted.close()
    with sqlite3.connect(path) as c:
        rows = dict(c.execute("SELECT id,value FROM objects"))
    assert rows["new"].startswith(MAGIC) and orjson.loads(rows["old"]) == {"name": "old"}
    # Disabling encryption again cannot silently read the encrypted rows.
    plain = SQLiteManagementStore(path)
    try:
        with pytest.raises(RuntimeError, match="encrypted"):
            await plain.list("a", "upstreams")
    finally:
        plain.close()


@pytest.mark.asyncio
async def test_user_filtering_and_deletion_preserves_other_users(tmp_path):
    management = SQLiteManagementStore(tmp_path / "management.sqlite3")
    kernel = SQLiteKernelStore(tmp_path / "kernel.sqlite3")
    try:
        await management.initialize()
        await kernel.initialize()
        await management.save("a", "upstreams", "shared", {"name": "shared"})
        for account, user in [("a", "alice"), ("a", "bob"), ("b", "alice")]:
            await management.save(account, "keys", user, {"user_id": user})
            await management.save(account, "users", user, {"user_id": user})
            await management.save(account, "responses", user, {"user_id": user}, ttl=3600)
            await management.log(account, {"request_id": user}, user_id=user)
            for protocol in ("chat", "responses", "anthropic"):
                scope = digest(account + "\0" + user + "\0" + protocol)
                with kernel.connect() as connection:
                    kernel.touch(connection, scope, "session")
        with management.connect() as connection:
            users = [r[0] for r in connection.execute("SELECT user FROM request_logs ORDER BY id")]
        assert users == ["alice", "bob", "alice"]
        assert len(await management.list("a", "keys", user_id="alice")) == 1
        assert len(await management.logs("a", user_id="alice")) == 1
        await delete_user_data(management, kernel, "a", "alice")
        assert await management.list("a", "keys", user_id="alice") == []
        assert await management.logs("a", user_id="alice") == []
        assert await management.get("a", "responses", "alice") is None
        assert len(await management.logs("a")) == 1
        assert len(await management.logs("b")) == 1
        assert len(await management.list("a", "upstreams")) == 1
        with kernel.connect() as connection:
            assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 6
        await delete_account_data(management, kernel, "a")
        assert await management.list("a", "upstreams") == []
        assert await management.logs("a") == []
        assert len(await management.list("b", "keys")) == 1
        with kernel.connect() as connection:
            assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 3
    finally:
        management.close()
        kernel.close()
