# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Dict, Iterable, List

INT64_SIZE = 8
UINT64_SIZE = 8
FLOAT32_SIZE = 4
UINT32_SIZE = 4
UINT16_SIZE = 2
BOOL_SIZE = 1


class FieldType(IntEnum):
    int64 = 0
    uint64 = 1
    float32 = 2
    string = 3
    binary = 4
    boolean = 5
    list_int64 = 6
    list_string = 7
    list_float32 = 8


class StorageOpType(IntEnum):
    PUT = 0
    DELETE = 1


@dataclass
class FieldMeta:
    name: str
    data_type: FieldType
    offset: int
    id: int
    default_value: Any = None


def _default_value_for_type(data_type: FieldType) -> Any:
    return {
        FieldType.int64: 0,
        FieldType.uint64: 0,
        FieldType.float32: 0.0,
        FieldType.string: "",
        FieldType.binary: b"",
        FieldType.boolean: False,
        FieldType.list_int64: [],
        FieldType.list_string: [],
        FieldType.list_float32: [],
    }[data_type]


def _field_type_size(data_type: FieldType) -> int:
    return {
        FieldType.int64: INT64_SIZE,
        FieldType.uint64: UINT64_SIZE,
        FieldType.float32: FLOAT32_SIZE,
        FieldType.string: UINT32_SIZE,
        FieldType.binary: UINT32_SIZE,
        FieldType.boolean: BOOL_SIZE,
        FieldType.list_int64: UINT32_SIZE,
        FieldType.list_string: UINT32_SIZE,
        FieldType.list_float32: UINT32_SIZE,
    }[data_type]


class Schema:
    def __init__(self, fields: list[dict[str, Any]]):
        if not isinstance(fields, list) or not fields:
            raise ValueError("Schema fields must be a non-empty list")

        self.field_metas: Dict[str, FieldMeta] = {}
        self.field_orders: List[FieldMeta] = [None] * len(fields)  # type: ignore[list-item]
        current_offset = 1
        seen_ids: set[int] = set()

        for field in fields:
            if not isinstance(field, dict):
                raise TypeError("Each schema field must be a dict")
            try:
                name = str(field["name"])
                data_type = FieldType(field["data_type"])
                field_id = int(field["id"])
            except KeyError as exc:
                raise ValueError(f"Missing schema field key: {exc.args[0]}") from exc
            except Exception as exc:  # pragma: no cover - defensive
                raise ValueError("Invalid schema field definition") from exc

            if not name:
                raise ValueError("Schema field name cannot be empty")
            if field_id < 0 or field_id >= len(fields):
                raise ValueError("Schema field ids must be contiguous and zero-based")
            if field_id in seen_ids:
                raise ValueError("Schema field ids must be unique")
            if name in self.field_metas:
                raise ValueError(f"Duplicate schema field name: {name}")

            seen_ids.add(field_id)
            default_value = field.get("default_value", _default_value_for_type(data_type))
            meta = FieldMeta(
                name=name,
                data_type=data_type,
                offset=current_offset,
                id=field_id,
                default_value=default_value,
            )
            self.field_metas[name] = meta
            self.field_orders[field_id] = meta
            current_offset += _field_type_size(data_type)

        if any(meta is None for meta in self.field_orders):
            raise ValueError("Schema field ids must be contiguous and zero-based")

        self.total_byte_length = current_offset

    def get_field_meta(self, field_name: str) -> FieldMeta:
        if field_name not in self.field_metas:
            raise KeyError(f"Field {field_name} does not exist in schema")
        return self.field_metas[field_name]

    def get_field_order(self) -> list[FieldMeta]:
        return self.field_orders

    def get_total_byte_length(self) -> int:
        return self.total_byte_length


def _get_row_value(row_data: Any, field_name: str, default_value: Any) -> Any:
    if isinstance(row_data, dict):
        return row_data.get(field_name, default_value)
    return getattr(row_data, field_name, default_value)


class BytesRow:
    def __init__(self, schema: Schema):
        self.schema = schema
        self.field_order = schema.get_field_order()

    def serialize(self, row_data: Any) -> bytes:
        fixed_formats: list[str] = []
        fixed_values: list[Any] = []
        variable_formats: list[str] = []
        variable_values: list[Any] = []
        variable_region_offset = self.schema.total_byte_length

        for field_meta in self.field_order:
            value = _get_row_value(row_data, field_meta.name, field_meta.default_value)

            if field_meta.data_type == FieldType.int64:
                fixed_formats.append("q")
                fixed_values.append(int(value))
            elif field_meta.data_type == FieldType.uint64:
                fixed_formats.append("Q")
                fixed_values.append(int(value))
            elif field_meta.data_type == FieldType.float32:
                fixed_formats.append("f")
                fixed_values.append(float(value))
            elif field_meta.data_type == FieldType.boolean:
                fixed_formats.append("B")
                fixed_values.append(1 if value else 0)
            elif field_meta.data_type == FieldType.string:
                encoded = str(value).encode("utf-8")
                fixed_formats.append("I")
                fixed_values.append(variable_region_offset)
                variable_formats.append("H")
                variable_values.append(len(encoded))
                variable_formats.append(f"{len(encoded)}s")
                variable_values.append(encoded)
                variable_region_offset += UINT16_SIZE + len(encoded)
            elif field_meta.data_type == FieldType.binary:
                blob = bytes(value)
                fixed_formats.append("I")
                fixed_values.append(variable_region_offset)
                variable_formats.append("I")
                variable_values.append(len(blob))
                variable_formats.append(f"{len(blob)}s")
                variable_values.append(blob)
                variable_region_offset += UINT32_SIZE + len(blob)
            elif field_meta.data_type == FieldType.list_int64:
                items = [int(item) for item in value]
                fixed_formats.append("I")
                fixed_values.append(variable_region_offset)
                variable_formats.append("H")
                variable_values.append(len(items))
                variable_formats.append(f"{len(items)}q")
                variable_values.extend(items)
                variable_region_offset += UINT16_SIZE + len(items) * INT64_SIZE
            elif field_meta.data_type == FieldType.list_float32:
                items = [float(item) for item in value]
                fixed_formats.append("I")
                fixed_values.append(variable_region_offset)
                variable_formats.append("H")
                variable_values.append(len(items))
                variable_formats.append(f"{len(items)}f")
                variable_values.extend(items)
                variable_region_offset += UINT16_SIZE + len(items) * FLOAT32_SIZE
            elif field_meta.data_type == FieldType.list_string:
                items = [str(item) for item in value]
                fixed_formats.append("I")
                fixed_values.append(variable_region_offset)
                variable_formats.append("H")
                variable_values.append(len(items))
                variable_region_offset += UINT16_SIZE
                for item in items:
                    encoded = item.encode("utf-8")
                    variable_formats.append("H")
                    variable_values.append(len(encoded))
                    variable_formats.append(f"{len(encoded)}s")
                    variable_values.append(encoded)
                    variable_region_offset += UINT16_SIZE + len(encoded)

        fmt = "<" + "".join(fixed_formats) + "".join(variable_formats)
        buffer = bytearray(1 + struct.calcsize(fmt))
        buffer[0] = len(self.field_order)
        struct.pack_into(fmt, buffer, 1, *(fixed_values + variable_values))
        return bytes(buffer)

    def serialize_batch(self, rows_data: Iterable[Any]) -> list[bytes]:
        return [self.serialize(row_data) for row_data in rows_data]

    def deserialize_field(self, serialized_data: bytes, field_name: str) -> Any:
        field_meta = self.schema.get_field_meta(field_name)
        if field_meta.id >= serialized_data[0]:
            return field_meta.default_value

        if field_meta.data_type == FieldType.int64:
            return struct.unpack_from("<q", serialized_data, field_meta.offset)[0]
        if field_meta.data_type == FieldType.uint64:
            return struct.unpack_from("<Q", serialized_data, field_meta.offset)[0]
        if field_meta.data_type == FieldType.float32:
            return struct.unpack_from("<f", serialized_data, field_meta.offset)[0]
        if field_meta.data_type == FieldType.boolean:
            return bool(serialized_data[field_meta.offset])
        if field_meta.data_type == FieldType.string:
            str_offset = struct.unpack_from("<I", serialized_data, field_meta.offset)[0]
            str_len = struct.unpack_from("<H", serialized_data, str_offset)[0]
            str_offset += UINT16_SIZE
            return serialized_data[str_offset : str_offset + str_len].decode("utf-8")
        if field_meta.data_type == FieldType.binary:
            blob_offset = struct.unpack_from("<I", serialized_data, field_meta.offset)[0]
            blob_len = struct.unpack_from("<I", serialized_data, blob_offset)[0]
            blob_offset += UINT32_SIZE
            return serialized_data[blob_offset : blob_offset + blob_len]
        if field_meta.data_type == FieldType.list_int64:
            list_offset = struct.unpack_from("<I", serialized_data, field_meta.offset)[0]
            list_len = struct.unpack_from("<H", serialized_data, list_offset)[0]
            list_offset += UINT16_SIZE
            return list(struct.unpack_from(f"<{list_len}q", serialized_data, list_offset))
        if field_meta.data_type == FieldType.list_float32:
            list_offset = struct.unpack_from("<I", serialized_data, field_meta.offset)[0]
            list_len = struct.unpack_from("<H", serialized_data, list_offset)[0]
            list_offset += UINT16_SIZE
            return list(struct.unpack_from(f"<{list_len}f", serialized_data, list_offset))
        if field_meta.data_type == FieldType.list_string:
            list_offset = struct.unpack_from("<I", serialized_data, field_meta.offset)[0]
            list_len = struct.unpack_from("<H", serialized_data, list_offset)[0]
            list_offset += UINT16_SIZE
            items = []
            for _ in range(list_len):
                item_len = struct.unpack_from("<H", serialized_data, list_offset)[0]
                list_offset += UINT16_SIZE
                items.append(serialized_data[list_offset : list_offset + item_len].decode("utf-8"))
                list_offset += item_len
            return items
        return None

    def deserialize(self, serialized_data: bytes) -> dict[str, Any]:
        return {
            field_meta.name: self.deserialize_field(serialized_data, field_meta.name)
            for field_meta in self.field_order
        }


class _RequestBase:
    __slots__ = ()

    def to_backend(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__slots__}


class AddDataRequest(_RequestBase):
    __slots__ = (
        "label",
        "vector",
        "sparse_raw_terms",
        "sparse_values",
        "fields_str",
        "old_fields_str",
    )

    def __init__(self):
        self.label = 0
        self.vector = []
        self.sparse_raw_terms = []
        self.sparse_values = []
        self.fields_str = ""
        self.old_fields_str = ""


class DeleteDataRequest(_RequestBase):
    __slots__ = ("label", "old_fields_str")

    def __init__(self):
        self.label = 0
        self.old_fields_str = ""


class SearchRequest(_RequestBase):
    __slots__ = ("query", "sparse_raw_terms", "sparse_values", "topk", "dsl")

    def __init__(self):
        self.query = []
        self.sparse_raw_terms = []
        self.sparse_values = []
        self.topk = 0
        self.dsl = ""


class SearchResult:
    __slots__ = ("result_num", "labels", "scores", "extra_json")

    def __init__(
        self,
        *,
        result_num: int = 0,
        labels: list[int] | None = None,
        scores: list[float] | None = None,
        extra_json: str = "",
    ):
        self.result_num = result_num
        self.labels = labels or []
        self.scores = scores or []
        self.extra_json = extra_json

    @classmethod
    def from_backend(cls, payload: dict[str, Any]) -> "SearchResult":
        return cls(
            result_num=int(payload.get("result_num", 0)),
            labels=list(payload.get("labels", [])),
            scores=list(payload.get("scores", [])),
            extra_json=str(payload.get("extra_json", "")),
        )


class StateResult:
    __slots__ = ("update_timestamp", "element_count")

    def __init__(self, *, update_timestamp: int = 0, element_count: int = 0):
        self.update_timestamp = update_timestamp
        self.element_count = element_count

    @property
    def data_count(self) -> int:
        return self.element_count

    @classmethod
    def from_backend(cls, payload: dict[str, Any]) -> "StateResult":
        count = int(payload.get("element_count", payload.get("data_count", 0)))
        return cls(
            update_timestamp=int(payload.get("update_timestamp", 0)),
            element_count=count,
        )


class StorageOp(_RequestBase):
    __slots__ = ("type", "key", "value")

    def __init__(self):
        self.type = StorageOpType.PUT
        self.key = ""
        self.value = b""


def _request_list_to_backend(items: Iterable[_RequestBase]) -> list[Any]:
    return list(items)


def _build_native_bytes_row_exports(backend: Any):
    class Schema:
        def __init__(self, fields: list[dict[str, Any]]):
            try:
                self._handle = backend._new_schema(fields)
            except RuntimeError as exc:
                raise ValueError(str(exc)) from exc

        def get_total_byte_length(self) -> int:
            return int(backend._schema_get_total_byte_length(self._handle))

    class BytesRow:
        def __init__(self, schema: Schema):
            self.schema = schema
            self._handle = backend._new_bytes_row(schema._handle)

        def serialize(self, row_data: Any) -> bytes:
            return bytes(backend._bytes_row_serialize(self._handle, row_data))

        def serialize_batch(self, rows_data: Iterable[Any]) -> list[bytes]:
            return list(backend._bytes_row_serialize_batch(self._handle, list(rows_data)))

        def deserialize(self, serialized_data: bytes) -> dict[str, Any]:
            return dict(backend._bytes_row_deserialize(self._handle, serialized_data))

        def deserialize_field(self, serialized_data: bytes, field_name: str) -> Any:
            return backend._bytes_row_deserialize_field(self._handle, serialized_data, field_name)

    return Schema, BytesRow


def build_abi3_exports(backend: Any) -> dict[str, Any]:
    if hasattr(backend, "_new_schema") and hasattr(backend, "_new_bytes_row"):
        SchemaExport, BytesRowExport = _build_native_bytes_row_exports(backend)
    else:  # pragma: no cover - temporary fallback
        SchemaExport, BytesRowExport = Schema, BytesRow

    class IndexEngine:
        def __init__(self, path_or_json: str):
            self._backend = backend
            self._handle = backend._new_index_engine(path_or_json)

        def add_data(self, data_list: list[AddDataRequest]) -> int:
            return int(
                self._backend._index_engine_add_data(
                    self._handle, _request_list_to_backend(data_list)
                )
            )

        def delete_data(self, data_list: list[DeleteDataRequest]) -> int:
            return int(
                self._backend._index_engine_delete_data(
                    self._handle, _request_list_to_backend(data_list)
                )
            )

        def search(self, req: SearchRequest) -> SearchResult:
            return SearchResult.from_backend(self._backend._index_engine_search(self._handle, req))

        def dump(self, path: str) -> int:
            return int(self._backend._index_engine_dump(self._handle, path))

        def get_state(self) -> StateResult:
            return StateResult.from_backend(self._backend._index_engine_get_state(self._handle))

    class _StoreBase:
        def __init__(self, handle: Any):
            self._backend = backend
            self._handle = handle

        def exec_op(self, ops: list[StorageOp]) -> int:
            return int(self._backend._store_exec_op(self._handle, _request_list_to_backend(ops)))

        def get_data(self, keys: list[str]) -> list[bytes]:
            return list(self._backend._store_get_data(self._handle, keys))

        def put_data(self, keys: list[str], values: list[bytes]) -> int:
            return int(self._backend._store_put_data(self._handle, keys, values))

        def delete_data(self, keys: list[str]) -> int:
            return int(self._backend._store_delete_data(self._handle, keys))

        def clear_data(self) -> int:
            return int(self._backend._store_clear_data(self._handle))

        def seek_range(self, start_key: str, end_key: str) -> list[tuple[str, bytes]]:
            return list(self._backend._store_seek_range(self._handle, start_key, end_key))

    class PersistStore(_StoreBase):
        def __init__(self, path: str):
            super().__init__(backend._new_persist_store(path))

    class VolatileStore(_StoreBase):
        def __init__(self):
            super().__init__(backend._new_volatile_store())

    def init_logging(
        log_level: str, log_output: str, log_format: str = "[%Y-%m-%d %H:%M:%S.%e] [%l] %v"
    ):
        return backend._init_logging(log_level, log_output, log_format)

    return {
        "FieldType": FieldType,
        "StorageOpType": StorageOpType,
        "Schema": SchemaExport,
        "BytesRow": BytesRowExport,
        "AddDataRequest": AddDataRequest,
        "DeleteDataRequest": DeleteDataRequest,
        "SearchRequest": SearchRequest,
        "SearchResult": SearchResult,
        "StateResult": StateResult,
        "StorageOp": StorageOp,
        "IndexEngine": IndexEngine,
        "PersistStore": PersistStore,
        "VolatileStore": VolatileStore,
        "init_logging": init_logging,
    }
