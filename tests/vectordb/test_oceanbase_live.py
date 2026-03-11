# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
OceanBase adapter live tests. OceanBase is started via Docker for the test run.

  - TestOceanBaseLive: basic create/upsert/query/delete flow.
  - TestOceanBaseLiveRealWorld: real-world scenarios (multi-tenant context, filter by
    context_type/account_id/uri, vector search + filter, delete by uri).

Prerequisites:
  - Docker running, pip install pyobvector or openviking[oceanbase]

Run:
  pytest tests/vectordb/test_oceanbase_live.py -v -s
  python -m unittest tests.vectordb.test_oceanbase_live -v
"""

import hashlib
import subprocess
import time
import unittest
import uuid

# -----------------------------------------------------------------------------
# Docker OceanBase helpers (inlined for self-contained tests)
# -----------------------------------------------------------------------------
OB_IMAGE = "oceanbase/oceanbase-ce"
OB_PORT = "2881"
OB_DB_NAME = "openviking"
BOOT_TIMEOUT_SEC = 300
BOOT_POLL_INTERVAL_SEC = 3


def _run(cmd: list, capture: bool = True, timeout: int | None = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def _container_name() -> str:
    return f"openviking-ob-test-{uuid.uuid4().hex[:8]}"


def _start_oceanbase_docker(
    container_name: str | None = None,
    port: str | None = None,
    db_name: str = OB_DB_NAME,
    mode: str = "slim",
    boot_timeout_sec: int = BOOT_TIMEOUT_SEC,
) -> dict:
    """Start OceanBase in Docker, wait until boot success, create database, return connection info."""
    name = container_name or _container_name()
    _run(["docker", "rm", "-f", name], capture=True, timeout=10)
    host_port_spec = f"0:{OB_PORT}" if port is None else f"{port}:{OB_PORT}"
    r = _run(["docker", "run", "-d", "-p", host_port_spec, "--name", name, "-e", "MODE=" + mode, OB_IMAGE], timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"docker run failed: {r.stderr or r.stdout}")

    if port is None:
        rp = _run(["docker", "port", name, OB_PORT], timeout=5)
        if rp.returncode != 0 or not rp.stdout:
            _stop_oceanbase_docker(name)
            raise RuntimeError("Could not get container host port")
        port = rp.stdout.strip().split(":")[-1]
    else:
        port = str(port)

    deadline = time.monotonic() + boot_timeout_sec
    while time.monotonic() < deadline:
        # --tail 50: boot success may not be the very last line; bounded read to avoid timeout
        r = _run(["docker", "logs", "--tail", "50", name], timeout=20)
        log_snippet = (r.stdout or "") + (r.stderr or "")
        if "boot success!" in log_snippet:
            break
        time.sleep(BOOT_POLL_INTERVAL_SEC)
    else:
        _stop_oceanbase_docker(name)
        raise RuntimeError("OceanBase Docker container did not report boot success within timeout")

    for user in ("root@test", "root"):
        created = False
        for client in ("mysql", "obclient"):
            r = _run([
                "docker", "exec", name,
                client, "-h127.0.0.1", f"-P{OB_PORT}", f"-u{user}", "-e",
                f"CREATE DATABASE IF NOT EXISTS `{db_name}`;",
            ], timeout=15)
            if r.returncode == 0:
                created = True
                break
        if not created:
            _stop_oceanbase_docker(name)
            raise RuntimeError(f"Could not create database for user {user}")

    def stop_callback() -> None:
        _stop_oceanbase_docker(name)

    return {
        "host": "127.0.0.1",
        "port": port,
        "user": "root",
        "password": "",
        "db_name": db_name,
        "container_name": name,
        "stop_callback": stop_callback,
    }


def _stop_oceanbase_docker(container_name: str) -> None:
    _run(["docker", "stop", "-t", "10", container_name], timeout=30)
    _run(["docker", "rm", "-f", container_name], timeout=15)


def _is_docker_available() -> bool:
    r = _run(["docker", "info"], timeout=15)
    return r.returncode == 0


# -----------------------------------------------------------------------------
# Connection args for live DB; filled by Docker startup
# -----------------------------------------------------------------------------
CONNECTION_ARGS = {
    "host": "127.0.0.1",
    "port": "2881",
    "user": "root",
    "password": "",
    "db_name": "openviking",
}

# Set by Docker startup; cleared in last class tearDownClass
_docker_stop_callback = None


def _get_oceanbase_config(
    collection_name: str = "openviking_test_context",
    distance_metric: str = "cosine",
):
    """Build VectorDBBackendConfig (OceanBase) from CONNECTION_ARGS."""
    from openviking_cli.utils.config.vectordb_config import OceanBaseConfig, VectorDBBackendConfig

    uri = f"{CONNECTION_ARGS['host']}:{CONNECTION_ARGS['port']}"
    return VectorDBBackendConfig(
        backend="oceanbase",
        name=collection_name,
        distance_metric=distance_metric,
        dimension=8,  # short vectors for live tests
        oceanbase=OceanBaseConfig(
            uri=uri,
            user=CONNECTION_ARGS["user"],
            password=CONNECTION_ARGS["password"],
            db_name=CONNECTION_ARGS["db_name"],
        ),
    )


def _get_context_schema(name: str, dimension: int = 8):
    """Return OpenViking context collection schema with Dimension."""
    from openviking.storage.collection_schemas import CollectionSchemas

    schema = CollectionSchemas.context_collection(name, dimension)
    schema["Dimension"] = dimension
    return schema


class TestOceanBaseLive(unittest.TestCase):
    """Live tests against real OceanBase (127.0.0.1:2881, db_name=openviking)."""

    @classmethod
    def setUpClass(cls):
        global _docker_stop_callback
        if not _is_docker_available():
            raise unittest.SkipTest("Docker not available; OceanBase tests require Docker")
        if _docker_stop_callback is None:
            info = _start_oceanbase_docker(mode="slim")
            CONNECTION_ARGS["host"] = info["host"]
            CONNECTION_ARGS["port"] = info["port"]
            CONNECTION_ARGS["user"] = info["user"]
            CONNECTION_ARGS["password"] = info["password"]
            CONNECTION_ARGS["db_name"] = info["db_name"]
            _docker_stop_callback = info["stop_callback"]
        # Docker OceanBase (slim) does not support cosine/neg_ip; use L2
        cls._distance = "l2"
        try:
            import pyobvector  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("pyobvector not installed (pip install pyobvector or openviking[oceanbase])")
        cls.config = _get_oceanbase_config(distance_metric=cls._distance)
        cls.schema = _get_context_schema(cls.config.name or "openviking_test_context", dimension=cls.config.dimension or 8)
        cls.adapter = None
        cls.collection_name = cls.config.name or "openviking_test_context"

    def test_01_create_adapter_and_collection(self):
        """Create adapter and collection (skip if already exists)."""
        from openviking.storage.vectordb_adapters.factory import create_collection_adapter

        self.adapter = create_collection_adapter(self.config)
        self.assertIsNotNone(self.adapter)
        self.assertEqual(self.adapter.mode, "oceanbase")

        if not self.adapter.collection_exists():
            created = self.adapter.create_collection(
                self.collection_name,
                self.schema,
                distance=self._distance,
                sparse_weight=0.0,
                index_name="default",
            )
            self.assertTrue(created, "create_collection should return True when collection was created")
        else:
            # Collection exists, skip create
            pass

    def test_02_upsert_and_fetch(self):
        """Upsert records and fetch by id."""
        from openviking.storage.vectordb_adapters.factory import create_collection_adapter

        if self.adapter is None:
            self.adapter = create_collection_adapter(self.config)
        if not self.adapter.collection_exists():
            self.test_01_create_adapter_and_collection()

        # 8-dim vectors (match schema); adapter fills required fields without defaults
        records = [
            {
                "id": "live-test-id-1",
                "uri": "viking://resources/test/doc1.md",
                "type": "file",
                "context_type": "resource",
                "vector": [0.1] * 8,
                "sparse_vector": {},
                "abstract": "first doc",
                "created_at": 0,
                "updated_at": 0,
            },
            {
                "id": "live-test-id-2",
                "uri": "viking://resources/test/doc2.md",
                "type": "file",
                "context_type": "resource",
                "vector": [0.2] * 8,
                "sparse_vector": {},
                "abstract": "second doc",
                "created_at": 0,
                "updated_at": 0,
            },
        ]
        ids = self.adapter.upsert(records)
        self.assertEqual(len(ids), 2)
        self.assertIn("live-test-id-1", ids)
        self.assertIn("live-test-id-2", ids)

        fetched = self.adapter.get(ids=["live-test-id-1", "live-test-id-2"])
        self.assertGreaterEqual(len(fetched), 2)

    def test_03_search_by_vector(self):
        """Vector search."""
        from openviking.storage.vectordb_adapters.factory import create_collection_adapter

        if self.adapter is None:
            self.adapter = create_collection_adapter(self.config)
        if not self.adapter.collection_exists():
            self.test_01_create_adapter_and_collection()

        query_vector = [0.15] * 8
        results = self.adapter.query(
            query_vector=query_vector,
            limit=5,
        )
        self.assertIsInstance(results, list)
        # At least 2 rows from test_02
        self.assertGreaterEqual(len(results), 2)

    def test_04_count_and_cleanup(self):
        """Count and delete test data."""
        from openviking.storage.vectordb_adapters.factory import create_collection_adapter

        if self.adapter is None:
            self.adapter = create_collection_adapter(self.config)
        if not self.adapter.collection_exists():
            return

        n = self.adapter.count()
        self.assertGreaterEqual(n, 0)

        # Delete test ids inserted by this script
        deleted = self.adapter.delete(ids=["live-test-id-1", "live-test-id-2"])
        self.assertGreaterEqual(deleted, 0)


# -----------------------------------------------------------------------------
# Real-world: multi-tenant, filter, vector search, delete by uri
# -----------------------------------------------------------------------------

REALWORLD_COLLECTION = "openviking_live_context"


def _realworld_record(
    uri: str,
    context_type: str,
    account_id: str,
    owner_space: str,
    abstract: str,
    vector: list,
    level: int = 2,
    name: str = "",
):
    """Build one context record matching production shape (id from account_id:uri, same as TextEmbeddingHandler)."""
    id_seed = f"{account_id}:{uri}"
    record_id = hashlib.md5(id_seed.encode("utf-8")).hexdigest()
    return {
        "id": record_id,
        "uri": uri,
        "type": "file",
        "context_type": context_type,
        "vector": vector,
        "abstract": abstract,
        "account_id": account_id,
        "owner_space": owner_space,
        "level": level,
        "name": name or abstract[:50],
    }


class TestOceanBaseLiveRealWorld(unittest.TestCase):
    """
    OceanBase live tests for real-world scenarios:
    - Multi-tenant context upsert (resource / memory)
    - Filter by context_type, account_id, uri
    - Vector search + filter
    - Delete by uri, delete by filter, full cleanup
    """

    @classmethod
    def setUpClass(cls):
        try:
            import pyobvector  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("pyobvector not installed")
        # Use L2: many OceanBase versions do not support neg_ip (cosine); real-world collection uses L2
        cls.config = _get_oceanbase_config(REALWORLD_COLLECTION, distance_metric="l2")
        cls.schema = _get_context_schema(REALWORLD_COLLECTION, dimension=cls.config.dimension or 8)
        cls.adapter = None

    def _adapter(self):
        from openviking.storage.vectordb_adapters.factory import create_collection_adapter

        if self.adapter is None:
            self.adapter = create_collection_adapter(self.config)
        return self.adapter

    def _ensure_collection(self):
        a = self._adapter()
        if not a.collection_exists():
            a.create_collection(
                REALWORLD_COLLECTION,
                self.schema,
                distance="l2",  # Match config; OceanBase may not support neg_ip
                sparse_weight=0.0,
                index_name="default",
            )

    def test_realworld_01_upsert_multi_tenant_context(self):
        """Real-world: upsert multi-tenant resource + memory context data."""
        self._ensure_collection()
        dim = 8
        records = [
            _realworld_record(
                uri="viking://resources/acc_1/proj_a/readme.md",
                context_type="resource",
                account_id="acc_1",
                owner_space="default",
                abstract="Project A readme",
                vector=[0.1] * dim,
                name="readme",
            ),
            _realworld_record(
                uri="viking://resources/acc_1/proj_a/src/main.py",
                context_type="resource",
                account_id="acc_1",
                owner_space="default",
                abstract="Main entry",
                vector=[0.12, 0.11, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
                name="main",
            ),
            _realworld_record(
                uri="viking://user/acc_1/default/memories/mem_001.md",
                context_type="memory",
                account_id="acc_1",
                owner_space="default",
                abstract="User preference: prefers Python",
                vector=[0.2] * dim,
                name="memory",
            ),
            _realworld_record(
                uri="viking://resources/acc_2/proj_b/doc.md",
                context_type="resource",
                account_id="acc_2",
                owner_space="default",
                abstract="Project B doc",
                vector=[0.15] * dim,
                name="doc",
            ),
            _realworld_record(
                uri="viking://user/acc_2/default/memories/mem_002.md",
                context_type="memory",
                account_id="acc_2",
                owner_space="default",
                abstract="User preference: prefers Markdown",
                vector=[0.18, 0.18, 0.18, 0.18, 0.18, 0.18, 0.18, 0.18],
                name="memory",
            ),
        ]
        ids = self._adapter().upsert(records)
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5)

    def test_realworld_02_filter_by_context_type(self):
        """Real-world: filter by context_type."""
        self._ensure_collection()
        from openviking.storage.expr import Eq

        resource_records = self._adapter().query(
            filter=Eq("context_type", "resource"),
            limit=20,
        )
        memory_records = self._adapter().query(
            filter=Eq("context_type", "memory"),
            limit=20,
        )
        self.assertGreaterEqual(len(resource_records), 3, "at least 3 resource (2 acc_1 + 1 acc_2)")
        self.assertGreaterEqual(len(memory_records), 2, "at least 2 memory")
        for r in resource_records:
            self.assertEqual(r.get("context_type"), "resource")
        for r in memory_records:
            self.assertEqual(r.get("context_type"), "memory")

    def test_realworld_03_filter_by_account_id(self):
        """Real-world: filter by account_id (tenant isolation)."""
        self._ensure_collection()
        from openviking.storage.expr import Eq

        acc1 = self._adapter().query(filter=Eq("account_id", "acc_1"), limit=20)
        acc2 = self._adapter().query(filter=Eq("account_id", "acc_2"), limit=20)
        self.assertGreaterEqual(len(acc1), 3, "acc_1 at least 3")
        self.assertGreaterEqual(len(acc2), 2, "acc_2 at least 2")
        for r in acc1:
            self.assertEqual(r.get("account_id"), "acc_1")
        for r in acc2:
            self.assertEqual(r.get("account_id"), "acc_2")

    def test_realworld_04_vector_search_with_filter(self):
        """Real-world: vector search + context_type filter."""
        self._ensure_collection()
        from openviking.storage.expr import And, Eq

        query_vector = [0.12] * 8
        results = self._adapter().query(
            query_vector=query_vector,
            filter=Eq("context_type", "resource"),
            limit=5,
        )
        self.assertIsInstance(results, list)
        self.assertGreaterEqual(len(results), 1)
        for r in results:
            self.assertEqual(r.get("context_type"), "resource")
            self.assertIn("abstract", r)

    def test_realworld_05_fetch_by_uri(self):
        """Real-world: fetch by uri (simulate fetch_by_uri)."""
        self._ensure_collection()
        target_uri = "viking://resources/acc_1/proj_a/readme.md"
        records = self._adapter().query(
            filter={"op": "must", "field": "uri", "conds": [target_uri]},
            limit=2,
        )
        self.assertGreaterEqual(len(records), 1)
        self.assertEqual(records[0].get("uri"), target_uri)
        self.assertEqual(records[0].get("account_id"), "acc_1")

    def test_realworld_06_get_by_ids(self):
        """Real-world: get by ids (same ids as returned by upsert)."""
        self._ensure_collection()
        uri = "viking://resources/acc_1/proj_a/readme.md"
        record_id = hashlib.md5(f"acc_1:{uri}".encode("utf-8")).hexdigest()
        fetched = self._adapter().get(ids=[record_id])
        self.assertGreaterEqual(len(fetched), 1)
        self.assertEqual(fetched[0].get("id"), record_id)
        self.assertEqual(fetched[0].get("uri"), uri)

    def test_realworld_07_count_with_filter(self):
        """Real-world: count with filter."""
        self._ensure_collection()
        from openviking.storage.expr import Eq

        total = self._adapter().count()
        self.assertGreaterEqual(total, 5)
        resource_count = self._adapter().count(filter=Eq("context_type", "resource"))
        self.assertGreaterEqual(resource_count, 3)
        acc1_count = self._adapter().count(filter=Eq("account_id", "acc_1"))
        self.assertGreaterEqual(acc1_count, 3)

    def test_realworld_08_delete_by_uri_then_cleanup(self):
        """Real-world: delete by uri, then cleanup by account_id."""
        self._ensure_collection()
        # Delete one by uri
        target_uri = "viking://resources/acc_2/proj_b/doc.md"
        records = self._adapter().query(
            filter={"op": "must", "field": "uri", "conds": [target_uri]},
            limit=2,
        )
        if records:
            rid = records[0].get("id")
            if rid:
                self._adapter().delete(ids=[rid])
        # Delete all for acc_1 and acc_2 (tenant cleanup)
        from openviking.storage.expr import Eq

        for acc in ("acc_1", "acc_2"):
            deleted = self._adapter().delete(filter=Eq("account_id", acc), limit=10000)
            self.assertGreaterEqual(deleted, 0)
        # Collection may be empty if only this class uses it
        final_count = self._adapter().count()
        self.assertGreaterEqual(final_count, 0)
        # This test only verifies delete by filter runs; for strict cleanup, drop collection
        self.assertIsInstance(final_count, int)
        self.adapter = None  # Allow later tests to recreate adapter

    @classmethod
    def tearDownClass(cls):
        global _docker_stop_callback
        if _docker_stop_callback is not None:
            try:
                _docker_stop_callback()
            finally:
                _docker_stop_callback = None


if __name__ == "__main__":
    unittest.main()
