# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable, Protocol, TypeVar, cast

from pydantic import BaseModel

from .models import EncodedValue, ReplayCodecError

T = TypeVar("T")
EncodeFunction = Callable[[Any], EncodedValue]
DecodeFunction = Callable[[EncodedValue], Any]


class ReplayCodec(Protocol[T]):
    @staticmethod
    def encode(value: T, encode: EncodeFunction) -> dict[str, Any]: ...

    @staticmethod
    def decode(payload: dict[str, Any], decode: DecodeFunction) -> T: ...


@dataclass(frozen=True, slots=True)
class CodecRegistration:
    value_type: type[Any]
    name: str
    codec: type[ReplayCodec[Any]]


_CODECS_BY_TYPE: dict[type[Any], CodecRegistration] = {}
_CODECS_BY_NAME: dict[str, CodecRegistration] = {}


def register_codec(
    value_type: type[T], name: str, codec_type: type[ReplayCodec[T]]
) -> type[ReplayCodec[T]]:
    existing = _CODECS_BY_NAME.get(name)
    if existing is not None and existing.value_type is not value_type:
        raise ReplayCodecError(
            f"Replay codec name {name!r} is already registered for {existing.value_type!r}"
        )
    registration = CodecRegistration(
        value_type=value_type,
        name=name,
        codec=cast(type[ReplayCodec[Any]], codec_type),
    )
    _CODECS_BY_TYPE[value_type] = registration
    _CODECS_BY_NAME[name] = registration
    return codec_type


def encode_value(value: Any) -> EncodedValue:
    if value is None:
        return {"type": "none"}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int):
        return {"type": "int", "value": value}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    if isinstance(value, str):
        return {"type": "str", "value": value}
    if isinstance(value, list):
        return {"type": "list", "items": [encode_value(item) for item in value]}
    if isinstance(value, tuple):
        return {"type": "tuple", "items": [encode_value(item) for item in value]}
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ReplayCodecError("Replay dictionaries must have string keys")
        return {
            "type": "dict",
            "items": {key: encode_value(item) for key, item in value.items()},
        }

    value_type = type(value)
    registration = _CODECS_BY_TYPE.get(value_type)
    if registration is not None:
        payload = registration.codec.encode(value, encode_value)
        if not isinstance(payload, dict):
            raise ReplayCodecError(f"Replay codec {registration.name!r} must return a dictionary")
        return {"type": "codec", "name": registration.name, "payload": payload}
    if isinstance(value, BaseModel):
        try:
            data = _pydantic_model_data(value)
        except Exception as error:
            raise ReplayCodecError(f"Failed to serialize Pydantic model {value_type!r}") from error
        return {
            "type": "pydantic_model",
            "module": value_type.__module__,
            "qualname": value_type.__qualname__,
            "data": encode_value(data),
        }
    raise ReplayCodecError(f"No replay codec registered for {value_type!r}")


def decode_value(encoded: EncodedValue) -> Any:
    if not isinstance(encoded, dict) or not isinstance(encoded.get("type"), str):
        raise ReplayCodecError("Encoded replay value must be a tagged dictionary")
    value_type = encoded["type"]
    if value_type == "none":
        return None
    if value_type == "bool":
        return _required_value(encoded, bool)
    if value_type == "int":
        value = _required_value(encoded, int)
        if isinstance(value, bool):
            raise ReplayCodecError("Invalid encoded integer")
        return value
    if value_type == "float":
        return _required_value(encoded, float)
    if value_type == "str":
        return _required_value(encoded, str)
    if value_type in {"list", "tuple"}:
        items = encoded.get("items")
        if not isinstance(items, list):
            raise ReplayCodecError(f"Invalid encoded {value_type}")
        decoded = [decode_value(item) for item in items]
        return decoded if value_type == "list" else tuple(decoded)
    if value_type == "dict":
        items = encoded.get("items")
        if not isinstance(items, dict) or any(not isinstance(key, str) for key in items):
            raise ReplayCodecError("Invalid encoded dictionary")
        return {key: decode_value(item) for key, item in items.items()}
    if value_type == "codec":
        name = encoded.get("name")
        payload = encoded.get("payload")
        if not isinstance(name, str) or not isinstance(payload, dict):
            raise ReplayCodecError("Invalid registered codec envelope")
        registration = _CODECS_BY_NAME.get(name)
        if registration is None:
            raise ReplayCodecError(f"Replay codec {name!r} is not registered")
        return registration.codec.decode(payload, decode_value)
    if value_type == "pydantic_model":
        module = encoded.get("module")
        qualname = encoded.get("qualname")
        data = encoded.get("data")
        if (
            not isinstance(module, str)
            or not isinstance(qualname, str)
            or not isinstance(data, dict)
        ):
            raise ReplayCodecError("Invalid Pydantic replay envelope")
        model_type = _resolve_pydantic_model(module, qualname)
        try:
            decoded_data = decode_value(data)
            if getattr(model_type, "__pydantic_root_model__", False):
                return model_type.model_validate(decoded_data)
            return model_type.model_validate(decoded_data)
        except Exception as error:
            raise ReplayCodecError(
                f"Failed to validate replay data as {module}.{qualname}"
            ) from error
    raise ReplayCodecError(f"Unknown replay value type {value_type!r}")


def _pydantic_model_data(value: BaseModel) -> Any:
    value_type = type(value)
    if getattr(value_type, "__pydantic_root_model__", False):
        return value.root
    data: dict[str, Any] = {}
    for name, field in value_type.model_fields.items():
        validation_alias = field.validation_alias
        if validation_alias is not None and not isinstance(validation_alias, str):
            raise ReplayCodecError(
                f"Pydantic model field {value_type.__qualname__}.{name} uses an unsupported "
                "validation alias and requires an explicit replay codec"
            )
        key = validation_alias or name
        if key in data:
            raise ReplayCodecError(f"Duplicate replay field key {key!r} for {value_type!r}")
        data[key] = getattr(value, name)
    if value.model_extra:
        for key, extra_value in value.model_extra.items():
            if key in data:
                raise ReplayCodecError(f"Duplicate replay field key {key!r} for {value_type!r}")
            data[key] = extra_value
    return data


def _resolve_pydantic_model(module: str, qualname: str) -> type[BaseModel]:
    if "<locals>" in qualname.split("."):
        raise ReplayCodecError(f"Pydantic replay type {module}.{qualname} is not importable")
    try:
        value: Any = import_module(module)
        for part in qualname.split("."):
            value = getattr(value, part)
    except (ImportError, AttributeError) as error:
        raise ReplayCodecError(
            f"Pydantic replay type {module}.{qualname} cannot be resolved"
        ) from error
    if not isinstance(value, type) or not issubclass(value, BaseModel):
        raise ReplayCodecError(f"Replay type {module}.{qualname} is not a Pydantic model")
    return value


def _required_value(encoded: EncodedValue, expected_type: type[T]) -> T:
    value = encoded.get("value")
    if not isinstance(value, expected_type):
        raise ReplayCodecError(f"Invalid encoded {expected_type.__name__}")
    return value
