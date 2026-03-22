import importlib
import importlib.util
import platform
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_INIT = REPO_ROOT / "openviking" / "storage" / "vectordb" / "engine" / "__init__.py"


def _install_package_stubs(monkeypatch):
    packages = {
        "openviking": REPO_ROOT / "openviking",
        "openviking.storage": REPO_ROOT / "openviking" / "storage",
        "openviking.storage.vectordb": REPO_ROOT / "openviking" / "storage" / "vectordb",
    }
    for name, path in packages.items():
        module = types.ModuleType(name)
        module.__path__ = [str(path)]  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, name, module)


def _load_engine_module(
    monkeypatch, *, machine, available_backends, cpu_variants, env_variant=None
):
    _install_package_stubs(monkeypatch)
    for backend_name in available_backends:
        monkeypatch.setitem(
            sys.modules,
            f"openviking.storage.vectordb.engine._{backend_name}",
            types.SimpleNamespace(
                BACKEND_NAME=backend_name,
                IndexEngine=f"IndexEngine:{backend_name}",
                PersistStore=f"PersistStore:{backend_name}",
                VolatileStore=f"VolatileStore:{backend_name}",
            ),
        )

    monkeypatch.setitem(
        sys.modules,
        "openviking.storage.vectordb.engine._x86_caps",
        types.SimpleNamespace(get_supported_variants=lambda: list(cpu_variants)),
    )

    monkeypatch.setattr(platform, "machine", lambda: machine)
    if env_variant is None:
        monkeypatch.delenv("OV_ENGINE_VARIANT", raising=False)
    else:
        monkeypatch.setenv("OV_ENGINE_VARIANT", env_variant)

    original_import_module = importlib.import_module
    original_find_spec = importlib.util.find_spec

    def fake_import_module(name, package=None):
        if package == "openviking.storage.vectordb.engine" and name.startswith("._"):
            qualified_name = importlib.util.resolve_name(name, package)
            if qualified_name in sys.modules:
                return sys.modules[qualified_name]
            raise ModuleNotFoundError(name)

        return original_import_module(name, package)

    def fake_find_spec(name, package=None):
        fullname = importlib.util.resolve_name(name, package) if name.startswith(".") else name
        if fullname == "openviking.storage.vectordb.engine._x86_caps":
            return object()
        if fullname.startswith("openviking.storage.vectordb.engine."):
            backend_name = fullname.rsplit(".", 1)[-1].lstrip("_")
            if backend_name in available_backends:
                return object()
            return None
        return original_find_spec(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    spec = importlib.util.spec_from_file_location(
        "openviking.storage.vectordb.engine",
        ENGINE_INIT,
        submodule_search_locations=[str(ENGINE_INIT.parent)],
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "openviking.storage.vectordb.engine", module)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_engine_module_with_backend(monkeypatch, *, machine, backend_name, backend_module):
    _install_package_stubs(monkeypatch)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    monkeypatch.delenv("OV_ENGINE_VARIANT", raising=False)
    monkeypatch.setitem(
        sys.modules,
        f"openviking.storage.vectordb.engine._{backend_name}",
        backend_module,
    )

    original_import_module = importlib.import_module
    original_find_spec = importlib.util.find_spec

    def fake_import_module(name, package=None):
        if package == "openviking.storage.vectordb.engine" and name == f"._{backend_name}":
            return backend_module
        return original_import_module(name, package)

    def fake_find_spec(name, package=None):
        fullname = importlib.util.resolve_name(name, package) if name.startswith(".") else name
        if fullname == f"openviking.storage.vectordb.engine._{backend_name}":
            return object()
        return original_find_spec(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    spec = importlib.util.spec_from_file_location(
        "openviking.storage.vectordb.engine",
        ENGINE_INIT,
        submodule_search_locations=[str(ENGINE_INIT.parent)],
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "openviking.storage.vectordb.engine", module)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_engine_loader_auto_selects_best_supported_x86_backend(monkeypatch):
    module = _load_engine_module(
        monkeypatch,
        machine="x86_64",
        available_backends={"x86_sse3", "x86_avx2", "x86_avx512"},
        cpu_variants={"x86_sse3", "x86_avx2"},
    )

    assert module.ENGINE_VARIANT == "x86_avx2"
    assert module.IndexEngine == "IndexEngine:x86_avx2"
    assert module.AVAILABLE_ENGINE_VARIANTS == ("x86_sse3", "x86_avx2", "x86_avx512")


def test_engine_loader_uses_native_backend_on_non_x86(monkeypatch):
    module = _load_engine_module(
        monkeypatch,
        machine="arm64",
        available_backends={"native"},
        cpu_variants=set(),
    )

    assert module.ENGINE_VARIANT == "native"
    assert module.PersistStore == "PersistStore:native"
    assert module.AVAILABLE_ENGINE_VARIANTS == ("native",)


def test_engine_loader_rejects_forced_unsupported_variant(monkeypatch):
    with pytest.raises(ImportError, match="x86_avx512"):
        _load_engine_module(
            monkeypatch,
            machine="x86_64",
            available_backends={"x86_sse3", "x86_avx2"},
            cpu_variants={"x86_sse3", "x86_avx2"},
            env_variant="x86_avx512",
        )


def test_engine_loader_wraps_abi3_backend_with_python_api(monkeypatch):
    calls = []
    engine_handle = object()
    store_handle = object()
    schema_handle = object()
    bytes_row_handle = object()

    backend = types.SimpleNamespace(
        BACKEND_NAME="native",
        _ENGINE_BACKEND_API="abi3-v1",
        _new_schema=lambda fields: calls.append(("new_schema", fields)) or schema_handle,
        _schema_get_total_byte_length=lambda handle: calls.append(("schema_total", handle)) or 123,
        _new_bytes_row=lambda handle: calls.append(("new_bytes_row", handle)) or bytes_row_handle,
        _bytes_row_serialize=lambda handle, row: calls.append(("bytes_row_serialize", handle, row))
        or b"blob",
        _bytes_row_serialize_batch=lambda handle, rows: calls.append(
            ("bytes_row_serialize_batch", handle, rows)
        )
        or [b"blob-a", b"blob-b"],
        _bytes_row_deserialize=lambda handle, payload: calls.append(
            ("bytes_row_deserialize", handle, payload)
        )
        or {"id": 42, "name": "viking"},
        _bytes_row_deserialize_field=lambda handle, payload, field_name: calls.append(
            ("bytes_row_deserialize_field", handle, payload, field_name)
        )
        or (42 if field_name == "id" else "viking"),
        _new_index_engine=lambda config: calls.append(("new_index_engine", config))
        or engine_handle,
        _index_engine_add_data=lambda handle, items: calls.append(("add_data", handle, items)) or 3,
        _index_engine_delete_data=lambda handle, items: calls.append(("delete_data", handle, items))
        or 2,
        _index_engine_search=lambda handle, req: calls.append(("search", handle, req))
        or {
            "result_num": 2,
            "labels": [101, 202],
            "scores": [0.9, 0.8],
            "extra_json": '{"ok":true}',
        },
        _index_engine_dump=lambda handle, path: calls.append(("dump", handle, path)) or 11,
        _index_engine_get_state=lambda handle: calls.append(("get_state", handle))
        or {"update_timestamp": 123, "element_count": 7},
        _new_persist_store=lambda path: calls.append(("new_persist_store", path)) or store_handle,
        _new_volatile_store=lambda: calls.append(("new_volatile_store",)) or store_handle,
        _store_exec_op=lambda handle, ops: calls.append(("store_exec_op", handle, ops)) or 1,
        _store_get_data=lambda handle, keys: calls.append(("store_get_data", handle, keys))
        or [b"one", b"two"],
        _store_put_data=lambda handle, keys, values: calls.append(
            ("store_put_data", handle, keys, values)
        )
        or 0,
        _store_delete_data=lambda handle, keys: calls.append(("store_delete_data", handle, keys))
        or 0,
        _store_clear_data=lambda handle: calls.append(("store_clear_data", handle)) or 0,
        _store_seek_range=lambda handle, start, end: calls.append(
            ("store_seek_range", handle, start, end)
        )
        or [("aa", b"11"), ("ab", b"22")],
        _init_logging=lambda level, output, fmt: calls.append(("init_logging", level, output, fmt)),
    )

    module = _load_engine_module_with_backend(
        monkeypatch,
        machine="arm64",
        backend_name="native",
        backend_module=backend,
    )

    assert module.ENGINE_VARIANT == "native"
    assert module.AVAILABLE_ENGINE_VARIANTS == ("native",)

    schema = module.Schema(
        [
            {"name": "id", "data_type": module.FieldType.int64, "id": 0},
            {"name": "name", "data_type": module.FieldType.string, "id": 1},
        ]
    )
    assert schema.get_total_byte_length() == 123
    row = module.BytesRow(schema)
    blob = row.serialize({"id": 42, "name": "viking"})
    assert blob == b"blob"
    assert row.serialize_batch([{"id": 1}, {"id": 2}]) == [b"blob-a", b"blob-b"]
    assert row.deserialize_field(blob, "id") == 42
    assert row.deserialize(blob)["name"] == "viking"

    module.init_logging("INFO", "stdout")
    index = module.IndexEngine("config-json")

    add_req = module.AddDataRequest()
    add_req.label = 9
    add_req.vector = [0.1, 0.2]
    add_req.fields_str = '{"name":"x"}'
    assert index.add_data([add_req]) == 3

    search_req = module.SearchRequest()
    search_req.query = [0.5, 0.4]
    search_req.topk = 5
    search_req.dsl = "{}"
    result = index.search(search_req)
    assert result.labels == [101, 202]
    assert result.scores == [0.9, 0.8]
    assert result.extra_json == '{"ok":true}'

    state = index.get_state()
    assert state.update_timestamp == 123
    assert state.element_count == 7
    assert state.data_count == 7

    store = module.PersistStore("/tmp/test-store")
    assert store.get_data(["k1", "k2"]) == [b"one", b"two"]
    assert store.seek_range("a", "b") == [("aa", b"11"), ("ab", b"22")]

    op = module.StorageOp()
    op.type = module.StorageOpType.PUT
    op.key = "abc"
    op.value = b"payload"
    assert store.exec_op([op]) == 1

    assert ("new_index_engine", "config-json") in calls
    assert ("new_persist_store", "/tmp/test-store") in calls
    search_calls = [entry for entry in calls if entry[0] == "search"]
    assert len(search_calls) == 1
    _, handle, payload = search_calls[0]
    assert handle is engine_handle
    assert payload is search_req
    assert payload.query == [0.5, 0.4]
    assert payload.topk == 5
    assert payload.dsl == "{}"

    add_calls = [entry for entry in calls if entry[0] == "add_data"]
    assert len(add_calls) == 1
    _, handle, payload = add_calls[0]
    assert handle is engine_handle
    assert payload == [add_req]
    assert payload[0].fields_str == '{"name":"x"}'

    new_schema_calls = [entry for entry in calls if entry[0] == "new_schema"]
    assert len(new_schema_calls) == 1
    assert ("schema_total", schema_handle) in calls
    assert ("new_bytes_row", schema_handle) in calls
    assert ("bytes_row_serialize", bytes_row_handle, {"id": 42, "name": "viking"}) in calls
