# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Field type mapping and conversion helpers for scalar indexing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional, Tuple, Type

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    create_model,
)

from openviking.storage.vectordb.utils.id_generator import generate_auto_id


def get_pydantic_type(field_type: str) -> Type:
    """Map internal field types to Pydantic/Python types."""
    mapping = {
        "int64": int,
        "float32": float,
        "string": str,
        "bool": bool,
        "list<string>": List[str],
        "list<int64>": List[int],
        "vector": List[float],
        "sparse_vector": Dict[str, float],
        "text": str,
        "path": str,
        "image": str,
        "video": Dict[str, Any],
        "date_time": str,  # Input is string, parsed later
        "geo_point": str,  # Input is string "lon,lat"
    }
    return mapping.get(field_type, Any)


def _split_str_list(v: Any) -> Any:
    """Helper to split string input for list fields."""
    if isinstance(v, str):
        return v.split(";")
    return v


class DataProcessor:
    ENGINE_SCALAR_TYPE_MAP: Dict[str, Optional[str]] = {
        "int64": "int64",
        "float32": "float32",
        "string": "string",
        "bool": "bool",
        "list<string>": "string",
        "list<int64>": "string",
        "vector": None,
        "sparse_vector": None,
        "text": "string",
        "path": "path",
        "image": None,
        "video": None,
        "date_time": "int64",
        "geo_point": "geo_point",
    }

    GEO_POINT_LON_SUFFIX = "_lon"
    GEO_POINT_LAT_SUFFIX = "_lat"

    def __init__(
        self,
        fields_dict: Optional[Dict[str, Any]] = None,
        tz_policy: str = "local",
        collection_name: str = "dynamic",
    ):
        self.fields_dict = fields_dict or {}
        self.tz_policy = tz_policy
        self.collection_name = collection_name
        self._validator_model = self._build_validator_model()

    def _build_validator_model(self) -> Type[BaseModel]:
        """Dynamically build a Pydantic model based on fields_dict."""
        field_definitions = {}

        # Define sensible defaults for scalar types to handle missing fields
        # This prevents validation errors when upstream doesn't provide all fields
        TYPE_DEFAULTS = {
            "int64": 0,
            "float32": 0.0,
            "string": "",
            "bool": False,
            "list<string>": [],
            "list<int64>": [],
            "text": "",
            "path": "",
            "date_time": "",
            "geo_point": "",
            "sparse_vector": {},
        }

        # Define validators capturing self for configuration
        def validate_dt(v: Optional[str]) -> Optional[str]:
            if not v:
                return v
            self.parse_datetime_to_epoch_ms(v)
            return v

        def validate_gp(v: Optional[str]) -> Optional[str]:
            if not v:
                return v
            self.parse_geo_point(v)
            return v

        for name, meta in self.fields_dict.items():
            field_type_str = self.normalize_field_type(meta.get("FieldType"))
            py_type = get_pydantic_type(field_type_str)
            default_val = meta.get("DefaultValue")

            # Apply specific validators
            if field_type_str == "date_time":
                py_type = Annotated[py_type, AfterValidator(validate_dt)]
            elif field_type_str == "geo_point":
                py_type = Annotated[py_type, AfterValidator(validate_gp)]
            elif field_type_str in ("list<string>", "list<int64>"):
                py_type = Annotated[py_type, BeforeValidator(_split_str_list)]

            field_args = {}
            if default_val is not None:
                field_args["default"] = default_val
            elif name == "AUTO_ID":
                field_args["default_factory"] = generate_auto_id
            else:
                # Use type-based default if available, otherwise mark as required
                if field_type_str in TYPE_DEFAULTS:
                    field_args["default"] = TYPE_DEFAULTS[field_type_str]
                else:
                    field_args["default"] = ...  # Required

            # Add constraints
            # if field_type_str == "string":
            #    field_args["max_length"] = 1024

            field_definitions[name] = (py_type, Field(**field_args))

        # extra='forbid' ensures no unknown fields are allowed
        config = {"extra": "forbid"}

        return create_model(
            f"DynamicData_{self.collection_name}", __config__=config, **field_definitions
        )

    def validate_and_process(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate data against schema, fill defaults, and perform type conversion.
        Returns the processed dictionary ready for storage.
        """
        # Pydantic Validation (Type check, Defaults, Unknown fields, Custom format checks)
        # model_validate will raise ValidationError on failure
        validated_obj = self._validator_model.model_validate(data)
        processed_data = validated_obj.model_dump()

        return processed_data

    @classmethod
    def normalize_field_type(cls, field_type: Any) -> str:
        if hasattr(field_type, "value"):
            return field_type.value
        return str(field_type)

    @classmethod
    def get_engine_scalar_type(cls, field_type: Any) -> Optional[str]:
        field_type_str = cls.normalize_field_type(field_type)
        return cls.ENGINE_SCALAR_TYPE_MAP.get(field_type_str)

    @classmethod
    def get_geo_point_engine_fields(cls, field_name: str) -> Tuple[str, str]:
        return f"{field_name}{cls.GEO_POINT_LON_SUFFIX}", f"{field_name}{cls.GEO_POINT_LAT_SUFFIX}"

    def build_scalar_index_meta(self, user_scalar_fields: List[str]) -> List[Dict[str, str]]:
        scalar_index: List[Dict[str, str]] = []
        for field_name in user_scalar_fields:
            meta = self.fields_dict.get(field_name)
            if not meta:
                continue
            field_type = self.normalize_field_type(meta.get("FieldType"))
            engine_type = self.get_engine_scalar_type(field_type)
            if not engine_type:
                continue
            if engine_type == "geo_point":
                lon_field, lat_field = self.get_geo_point_engine_fields(field_name)
                if lon_field in self.fields_dict or lat_field in self.fields_dict:
                    raise ValueError(
                        f"geo_point index field name conflict: {lon_field} or {lat_field} already exists"
                    )
                scalar_index.append({"FieldName": lon_field, "FieldType": "float32"})
                scalar_index.append({"FieldName": lat_field, "FieldType": "float32"})
            else:
                scalar_index.append({"FieldName": field_name, "FieldType": engine_type})
        return scalar_index

    def user_scalar_fields_from_engine(self, engine_scalar_meta: List[Dict[str, str]]) -> List[str]:
        engine_fields = {item.get("FieldName") for item in engine_scalar_meta}
        scalar_fields: List[str] = []
        for field_name, meta in self.fields_dict.items():
            field_type = self.normalize_field_type(meta.get("FieldType"))
            engine_type = self.get_engine_scalar_type(field_type)
            if not engine_type:
                continue
            if engine_type == "geo_point":
                lon_field, lat_field = self.get_geo_point_engine_fields(field_name)
                if lon_field in engine_fields and lat_field in engine_fields:
                    scalar_fields.append(field_name)
            else:
                if field_name in engine_fields:
                    scalar_fields.append(field_name)
        return scalar_fields

    def parse_datetime_to_epoch_ms(self, value: Any) -> int:
        if isinstance(value, (int, float)):
            return int(value)
        if not isinstance(value, str):
            raise ValueError(
                f"date_time value must be string or number, got {type(value).__name__}"
            )
        raw = value.strip()
        if not raw:
            raise ValueError("date_time value is empty")
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"invalid date_time format: {value}") from exc
        if dt.tzinfo is None:
            if self.tz_policy == "local":
                local_tz = datetime.now().astimezone().tzinfo
                dt = dt.replace(tzinfo=local_tz)
            elif self.tz_policy == "utc":
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                raise ValueError(f"unknown tz_policy: {self.tz_policy}")
        return int(dt.timestamp() * 1000)

    def parse_geo_point(self, value: str) -> Tuple[float, float]:
        if not isinstance(value, str):
            raise ValueError(f"geo_point value must be string, got {type(value).__name__}")
        raw = value.strip()
        if not raw:
            raise ValueError("geo_point value is empty")
        parts = raw.split(",")
        if len(parts) != 2:
            raise ValueError("geo_point must be in 'lon,lat' format")
        try:
            lon = float(parts[0].strip())
            lat = float(parts[1].strip())
        except ValueError as exc:
            raise ValueError("geo_point lon/lat must be float") from exc
        if not (-180.0 < lon < 180.0):
            raise ValueError("geo_point longitude out of range (-180, 180)")
        if not (-90.0 < lat < 90.0):
            raise ValueError("geo_point latitude out of range (-90, 90)")
        return lon, lat

    def parse_radius(self, value: Any) -> float:
        if isinstance(value, (int, float)):
            # Assume meters if number is passed, convert to degrees approx
            return float(value) / 111320.0
        if not isinstance(value, str):
            raise ValueError(f"radius must be string, got {type(value).__name__}")
        raw = value.strip().lower()
        meters = 0.0
        if raw.endswith("km"):
            num = raw[:-2].strip()
            meters = float(num) * 1000.0
        elif raw.endswith("m"):
            num = raw[:-1].strip()
            meters = float(num)
        else:
            try:
                meters = float(raw)
            except ValueError:
                raise ValueError("radius must end with 'm' or 'km' or be a number")

        # Convert meters to degrees (1 degree ~= 111.32 km at equator)
        # This is a rough approximation for Euclidean engine on lon/lat
        return meters / 111320.0

    def convert_fields_dict_for_index(self, field_data_dict: Dict[str, Any]) -> Dict[str, Any]:
        if not field_data_dict:
            return field_data_dict
        converted = dict(field_data_dict)
        for field_name, value in field_data_dict.items():
            meta = self.fields_dict.get(field_name)
            if not meta:
                continue
            field_type = self.normalize_field_type(meta.get("FieldType"))
            if field_type == "date_time":
                if value in (None, ""):
                    converted.pop(field_name, None)
                    continue
                converted[field_name] = self.parse_datetime_to_epoch_ms(value)
            elif field_type == "geo_point":
                if value in (None, ""):
                    converted.pop(field_name, None)
                    continue
                lon, lat = self.parse_geo_point(value)
                lon_field, lat_field = self.get_geo_point_engine_fields(field_name)
                converted.pop(field_name, None)
                converted[lon_field] = float(lon)
                converted[lat_field] = float(lat)
            elif field_type == "list<string>":
                if value is None:
                    converted.pop(field_name, None)
                    continue
                if isinstance(value, list):
                    converted[field_name] = value
                elif isinstance(value, str):
                    converted[field_name] = value
                else:
                    raise ValueError("list<string> must be list or ';' joined string")
            elif field_type == "list<int64>":
                if value is None:
                    converted.pop(field_name, None)
                    continue
                if isinstance(value, list):
                    converted[field_name] = value
                elif isinstance(value, str):
                    converted[field_name] = value
                else:
                    raise ValueError("list<int64> must be list or ';' joined string")
        return converted

    def convert_fields_for_index(self, fields_json: str) -> str:
        if not fields_json:
            return fields_json
        data = json.loads(fields_json)
        converted = self.convert_fields_dict_for_index(data)
        return json.dumps(converted, ensure_ascii=False)

    def _convert_time_range_node(self, node: Dict[str, Any], field_type: str) -> Dict[str, Any]:
        if field_type != "date_time":
            return node
        if node.get("op") == "time_range":
            node["op"] = "range"
        for key in ("gt", "gte", "lt", "lte"):
            if key in node and node[key] is not None:
                node[key] = self.parse_datetime_to_epoch_ms(node[key])
        return node

    def _convert_geo_range_node(self, node: Dict[str, Any]) -> Dict[str, Any]:
        field = node.get("field")
        if isinstance(field, str):
            meta = self.fields_dict.get(field)
            if meta:
                field_type = self.normalize_field_type(meta.get("FieldType"))
                if field_type != "geo_point":
                    raise ValueError("geo_range field must be geo_point")
        if isinstance(field, list):
            fields = field
        else:
            fields = []
            if isinstance(field, str):
                lon_field, lat_field = self.get_geo_point_engine_fields(field)
                fields = [lon_field, lat_field]
        if fields:
            node["field"] = fields
        center = node.get("center")
        if isinstance(center, str):
            lon, lat = self.parse_geo_point(center)
            node["center"] = [lon, lat]
        radius = node.get("radius")
        if radius is not None:
            node["radius"] = self.parse_radius(radius)
        return node

    def _convert_field_conds(self, node: Dict[str, Any]) -> Dict[str, Any]:
        field = node.get("field")
        if not isinstance(field, str):
            return node
        meta = self.fields_dict.get(field)
        if not meta:
            return node
        field_type = self.normalize_field_type(meta.get("FieldType"))
        if field_type != "date_time":
            return node
        conds = node.get("conds")
        if not isinstance(conds, list):
            return node
        new_conds = []
        for item in conds:
            new_conds.append(self.parse_datetime_to_epoch_ms(item))
        node["conds"] = new_conds
        return node

    def _convert_range_node(self, node: Dict[str, Any]) -> Dict[str, Any]:
        field = node.get("field")
        if isinstance(field, list):
            return node
        if not isinstance(field, str):
            return node
        meta = self.fields_dict.get(field)
        if not meta:
            return node
        field_type = self.normalize_field_type(meta.get("FieldType"))
        res = self._convert_time_range_node(node, field_type)
        return res

    def _convert_filter_node(self, node: Dict[str, Any]) -> Dict[str, Any]:
        op = node.get("op")
        if op in ("and", "or"):
            conds = node.get("conds")
            if isinstance(conds, list):
                new_conds = []
                for cond in conds:
                    if isinstance(cond, dict):
                        new_conds.append(self._convert_filter_node(dict(cond)))
                    else:
                        new_conds.append(cond)
                node["conds"] = new_conds
            return node
        if op in ("must", "must_not", "prefix", "contains", "regex"):
            return self._convert_field_conds(node)
        if op in ("range", "range_out", "time_range"):
            return self._convert_range_node(node)
        if op == "geo_range":
            return self._convert_geo_range_node(node)
        return node

    def convert_filter_for_index(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        if not filters:
            return filters
        if "filter" in filters or "sorter" in filters:
            converted = dict(filters)
            if "filter" in converted and isinstance(converted["filter"], dict):
                converted["filter"] = self.convert_filter_for_index(converted["filter"])
            return converted
        return self._convert_filter_node(dict(filters))
