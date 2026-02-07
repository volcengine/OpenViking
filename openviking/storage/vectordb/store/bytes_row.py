# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import struct
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List

# Type size constants
INT64_SIZE = 8
UINT64_SIZE = 8
FLOAT32_SIZE = 4
UINT32_SIZE = 4  # Used for string/binary length and offset
UINT16_SIZE = 2  # Used for list length and string/binary length inside lists
BOOL_SIZE = 1


@dataclass
class FieldMeta:
    """Field metadata: records field encoding rules and position."""

    name: str
    data_type: "_PyFieldType"
    offset: int  # Start offset (calculated from the beginning of row data)
    id: int
    default_value: Any = None


class _PyFieldType(Enum):
    int64 = 0
    uint64 = 1
    float32 = 2
    string = 3
    binary = 4
    boolean = 5
    list_int64 = 6
    list_string = 7
    list_float32 = 8


class _PySchema:
    """Row data schema: manages metadata for all fields and calculates offsets."""

    def __init__(self, fields):
        """
        Initialize schema.
        fields example:
        [
            {"name": "id", "data_type": _PyFieldType.int64, "id": 0},
            {"name": "score", "data_type": _PyFieldType.float32, "id": 1},
            {"name": "name", "data_type": _PyFieldType.string, "id": 2},
            {"name": "is_pass", "data_type": _PyFieldType.boolean, "id": 3}
        ]
        """
        self.field_metas: Dict[str, FieldMeta] = {}
        self.field_orders: List[FieldMeta] = [None] * len(fields)  # type: ignore
        current_offset = 1

        # Type to size and default value mapping
        TYPE_INFO = {
            _PyFieldType.int64: (INT64_SIZE, 0),
            _PyFieldType.uint64: (UINT64_SIZE, 0),
            _PyFieldType.float32: (FLOAT32_SIZE, 0.0),
            _PyFieldType.string: (UINT32_SIZE, "default"),
            _PyFieldType.binary: (UINT32_SIZE, b""),
            _PyFieldType.boolean: (BOOL_SIZE, False),
            _PyFieldType.list_int64: (UINT32_SIZE, [0]),
            _PyFieldType.list_string: (UINT32_SIZE, ["default"]),
            _PyFieldType.list_float32: (UINT32_SIZE, [0.0]),
        }

        for field in fields:
            name = field["name"]
            data_type = field["data_type"]
            field_id = field["id"]

            if data_type not in TYPE_INFO:
                raise ValueError(f"Unsupported data type: {data_type}")

            byte_len, default_value = TYPE_INFO[data_type]

            # Optional default value override
            if "default_value" in field:
                default_value = field["default_value"]

            # Create field metadata and record offset
            self.field_metas[name] = FieldMeta(
                name=name,
                data_type=data_type,
                offset=current_offset,
                id=field_id,
                default_value=default_value,
            )
            self.field_orders[field_id] = self.field_metas[name]
            # Update start offset for the next field
            current_offset += byte_len

        self.total_byte_length = current_offset  # Total byte length per row data

    def get_field_meta(self, field_name: str) -> FieldMeta:
        """Get field metadata (raises error if not exists)."""
        if field_name not in self.field_metas:
            raise KeyError(f"Field {field_name} does not exist in schema")
        return self.field_metas[field_name]

    def get_field_order(self) -> List[FieldMeta]:
        """Get field definition order (for order matching during serialization/deserialization)."""
        return self.field_orders


class _PyBytesRow:
    def __init__(self, schema: _PySchema):
        self.schema = schema
        self.field_order = schema.get_field_order()

    def serialize(self, row_data) -> bytes:
        fix_fmt_list = []
        fix_val_list = []
        var_fmt_list = []
        var_val_list = []
        fix_region_offset = 1
        variable_region_offset = self.schema.total_byte_length

        for field_meta in self.field_order:
            field_name = field_meta.name

            value = row_data[field_name] if field_name in row_data else field_meta.default_value
            if field_meta.data_type == _PyFieldType.int64:
                fix_fmt_list.append("q")
                fix_val_list.append(value)
                fix_region_offset += INT64_SIZE
            elif field_meta.data_type == _PyFieldType.uint64:
                fix_fmt_list.append("Q")
                fix_val_list.append(value)
                fix_region_offset += UINT64_SIZE
            elif field_meta.data_type == _PyFieldType.float32:
                fix_fmt_list.append("f")
                fix_val_list.append(value)
                fix_region_offset += FLOAT32_SIZE
            elif field_meta.data_type == _PyFieldType.boolean:
                fix_fmt_list.append("B")
                fix_val_list.append(int(value))
                fix_region_offset += BOOL_SIZE
            elif field_meta.data_type == _PyFieldType.string:
                fix_fmt_list.append("I")
                fix_val_list.append(variable_region_offset)
                fix_region_offset += UINT32_SIZE
                bytes_item = value.encode("utf-8")
                bytes_item_len = len(bytes_item)
                var_fmt_list.append("H")
                var_val_list.append(bytes_item_len)
                variable_region_offset += UINT16_SIZE
                var_fmt_list.append(f"{bytes_item_len}s")
                var_val_list.append(bytes_item)
                variable_region_offset += bytes_item_len
            elif field_meta.data_type == _PyFieldType.binary:
                fix_fmt_list.append("I")
                fix_val_list.append(variable_region_offset)
                fix_region_offset += UINT32_SIZE
                var_fmt_list.append("I")
                var_val_list.append(len(value))
                variable_region_offset += UINT32_SIZE
                var_fmt_list.append(f"{len(value)}s")
                var_val_list.append(value)
                variable_region_offset += len(value)
            elif field_meta.data_type == _PyFieldType.list_int64:
                fix_fmt_list.append("I")
                fix_val_list.append(variable_region_offset)
                fix_region_offset += UINT32_SIZE
                var_fmt_list.append("H")
                value_len = len(value)
                var_val_list.append(value_len)
                var_fmt_list.append(f"{value_len}q")
                var_val_list.extend(value)
                variable_region_offset += UINT16_SIZE + len(value) * INT64_SIZE
            elif field_meta.data_type == _PyFieldType.list_float32:
                fix_fmt_list.append("I")
                fix_val_list.append(variable_region_offset)
                fix_region_offset += UINT32_SIZE
                var_fmt_list.append("H")
                value_len = len(value)
                var_val_list.append(value_len)
                var_fmt_list.append(f"{value_len}f")
                var_val_list.extend(value)
                variable_region_offset += UINT16_SIZE + len(value) * FLOAT32_SIZE

            elif field_meta.data_type == _PyFieldType.list_string:
                fix_fmt_list.append("I")
                fix_val_list.append(variable_region_offset)
                fix_region_offset += UINT32_SIZE
                var_fmt_list.append("H")
                value_len = len(value)
                var_val_list.append(value_len)
                variable_region_offset += UINT16_SIZE
                for item in value:
                    bytes_item = item.encode("utf-8")
                    bytes_item_len = len(bytes_item)
                    var_fmt_list.append("H")
                    var_val_list.append(bytes_item_len)
                    var_fmt_list.append(f"{bytes_item_len}s")
                    var_val_list.append(bytes_item)
                    variable_region_offset += UINT16_SIZE + bytes_item_len

        # Use '<' for little-endian
        fmt = "<" + "".join(fix_fmt_list) + "".join(var_fmt_list)
        buffer = bytearray(1 + struct.calcsize(fmt))
        buffer[0] = len(self.field_order)  # <= 255
        struct.pack_into(fmt, buffer, 1, *(fix_val_list + var_val_list))
        return bytes(buffer)

    def serialize_batch(self, rows_data) -> List[bytes]:
        return [self.serialize(row_data) for row_data in rows_data]

    def deserialize_field(self, serialized_data, field_name):
        field_meta = self.schema.get_field_meta(field_name)
        if field_meta.id >= serialized_data[0]:
            return field_meta.default_value

        # Use '<' for little-endian in all unpack operations
        if field_meta.data_type == _PyFieldType.int64:
            return struct.unpack_from("<q", serialized_data, field_meta.offset)[0]
        elif field_meta.data_type == _PyFieldType.uint64:
            return struct.unpack_from("<Q", serialized_data, field_meta.offset)[0]
        elif field_meta.data_type == _PyFieldType.float32:
            return struct.unpack_from("<f", serialized_data, field_meta.offset)[0]
        elif field_meta.data_type == _PyFieldType.boolean:
            # B is 1 byte, endianness doesn't matter, but consistent style
            return bool(serialized_data[field_meta.offset])
        elif field_meta.data_type == _PyFieldType.string:
            str_offset = struct.unpack_from("<I", serialized_data, field_meta.offset)[0]
            str_len = struct.unpack_from("<H", serialized_data, str_offset)[0]
            str_offset += UINT16_SIZE
            return serialized_data[str_offset : str_offset + str_len].decode("utf-8")
        elif field_meta.data_type == _PyFieldType.binary:
            binary_offset = struct.unpack_from("<I", serialized_data, field_meta.offset)[0]
            binary_len = struct.unpack_from("<I", serialized_data, binary_offset)[0]
            binary_offset += UINT32_SIZE
            return serialized_data[binary_offset : binary_offset + binary_len]
        elif field_meta.data_type == _PyFieldType.list_string:
            list_offset = struct.unpack_from("<I", serialized_data, field_meta.offset)[0]
            list_len = struct.unpack_from("<H", serialized_data, list_offset)[0]
            list_offset += UINT16_SIZE
            str_list = [None] * list_len
            for i in range(list_len):
                str_len = struct.unpack_from("<H", serialized_data, list_offset)[0]
                list_offset += UINT16_SIZE
                str_list[i] = serialized_data[list_offset : list_offset + str_len].decode("utf-8")
                list_offset += str_len
            return str_list
        elif field_meta.data_type == _PyFieldType.list_int64:
            list_offset = struct.unpack_from("<I", serialized_data, field_meta.offset)[0]
            list_len = struct.unpack_from("<H", serialized_data, list_offset)[0]
            list_offset += UINT16_SIZE
            return list(struct.unpack_from(f"<{list_len}q", serialized_data, list_offset))

        elif field_meta.data_type == _PyFieldType.list_float32:
            list_offset = struct.unpack_from("<I", serialized_data, field_meta.offset)[0]
            list_len = struct.unpack_from("<H", serialized_data, list_offset)[0]
            list_offset += UINT16_SIZE
            return list(struct.unpack_from(f"<{list_len}f", serialized_data, list_offset))

        return None

    def deserialize(self, serialized_data):
        data_dict = {}
        for field_meta in self.schema.get_field_order():
            field_name = field_meta.name
            value = self.deserialize_field(serialized_data, field_name)
            if value is not None:
                data_dict[field_name] = value

        return data_dict


try:
    import openviking.storage.vectordb.engine as engine

    # Use C++ implementation if available
    BytesRow = engine.BytesRow
    Schema = engine.Schema
    FieldType = engine.FieldType
except ImportError:
    # Fallback to Python implementation
    BytesRow = _PyBytesRow
    Schema = _PySchema
    FieldType = _PyFieldType
