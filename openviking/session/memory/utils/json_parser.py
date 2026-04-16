# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
JSON stable parsing - Five-Layer Fault Tolerance Architecture.

Layer 1: JSON Cleanup    - extract_json_content()
Layer 2: JSON Repair      - json_repair.loads() (handles markdown too)
Layer 3: Structure Tolerance - list→object conversion + field filtering
Layer 4: Value Tolerance  - value_fault_tolerance()
Layer 5: Validation Tolerance - TypeAdapter(strict=False) + list item filtering
"""

import json
from dataclasses import is_dataclass, asdict
from types import UnionType
from typing import (
    Any,
    List,
    Optional,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

import json_repair
from pydantic import TypeAdapter, BaseModel, parse_obj_as


from openviking_cli.utils import get_logger

logger = get_logger(__name__)


# Exported for testing
__all__ = [
    "extract_json_content",
    "remove_json_trailing_content",
    "parse_json_with_stability",
    "value_fault_tolerance",
    "parse_value_with_tolerance",
    "_get_origin_type",
    "_get_arg_type",
    "_any_to_str",
    "JsonUtils",
]




class PydanticEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, BaseModel) :
            # 保存类名和属性值
            return {
                **obj.model_dump(mode='python')
            }
        elif is_dataclass(obj):
            return asdict(obj)
        return super().default(obj)



class JsonUtils:

    @staticmethod
    def dumps(obj, indent=4, ensure_ascii=False):
        if obj is None:
            return None
        return json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent, cls=PydanticEncoder)

    @staticmethod
    def loads(json_str, clazz=None):
        if not json_str:
            return None
        if clazz:
            return TypeAdapter.validate_python(clazz, json_repair.loads(json_str))
        return json_repair.loads(json_str)


def extract_json_content(s: str) -> str:
    """
    Layer 1: Extract JSON content from LLM response, removing both leading and trailing non-JSON content.

    Models often add thinking process, safety warnings, or explanations before or after the JSON.
    This function extracts only the valid JSON part from first {/[ to last }/].

    Args:
        s: Raw LLM response string

    Returns:
        String with only the JSON part (from first {/[ to last }/])
    """
    if not s:
        return s

    original_stripped = s.strip()
    if not original_stripped:
        return s

    temp_s = s

    # Find first { or [
    first_brace = temp_s.find("{")
    first_bracket = temp_s.find("[")

    start_idx = 0
    if first_brace != -1 and first_bracket != -1:
        start_idx = min(first_brace, first_bracket)
    elif first_brace != -1:
        start_idx = first_brace
    elif first_bracket != -1:
        start_idx = first_bracket
    else:
        # No JSON markers found, return original
        return s

    if start_idx > 0:
        temp_s = temp_s[start_idx:]

    # Find last } or ]
    last_brace = temp_s.rfind("}")
    last_bracket = temp_s.rfind("]")

    end_idx = len(temp_s)
    if last_brace != -1 and last_bracket != -1:
        end_idx = max(last_brace, last_bracket) + 1
    elif last_brace != -1:
        end_idx = last_brace + 1
    elif last_bracket != -1:
        end_idx = last_bracket + 1

    if end_idx < len(temp_s):
        temp_s = temp_s[:end_idx]

    result = temp_s.strip()

    # If we stripped everything, return original
    if not result:
        return s

    return result


def remove_json_trailing_content(s: str) -> str:
    """
    Layer 1: Remove extra content after JSON closing brace.

    DEPRECATED: Use extract_json_content() instead which handles both leading and trailing content.

    Args:
        s: Raw LLM response string

    Returns:
        String with only the JSON part
    """
    return extract_json_content(s)


def _get_origin_type(annotation) -> Type:
    """
    Extract base type from Optional or Union types.

    Similar to BaseModelCompat.get_origin_type().

    Args:
        annotation: Type annotation (could be Union, Optional, List, etc.)

    Returns:
        The underlying origin type
    """
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        args = get_args(annotation)
        # Handle Optional[T] which is Union[T, None]
        if len(args) == 2 and args[1] == type(None):
            return _get_origin_type(args[0])
    elif origin is list:
        return list
    return annotation


def _get_arg_type(annotation) -> Optional[Type]:
    """
    Extract item type from List annotations.

    Similar to BaseModelCompat.get_arg_type().

    Args:
        annotation: Type annotation

    Returns:
        The list item type if annotation is List[T], else None
    """
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        args = get_args(annotation)
        if len(args) == 2 and args[1] == type(None):
            return _get_arg_type(args[0])
    elif origin is list:
        args = get_args(annotation)
        if args:
            return args[0]
    return None


def _any_to_str(value) -> str:
    """
    Convert any value to string, with special handling for containers.

    Similar to BaseModelCompat.any_to_str().

    Args:
        value: Any value

    Returns:
        String representation
    """
    if value is None:
        return ""
    if isinstance(value, list):
        return ",".join(map(str, value))
    elif isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    elif isinstance(value, (int, bool, float)):
        return f"{value}"
    return str(value)


def value_fault_tolerance(field_type, value):
    """
    Layer 4: Value-level fault tolerance - automatic type conversion.

    Similar to BaseModelCompat.value_fault_tolerance().

    Handles common type mismatches:
    - 'None' → None (for non-str types)
    - list/dict/number → str (when target type is str)
    - str → int/float (when target type is number)
    - str/dict → list (when target type is list)

    Args:
        field_type: Target type annotation
        value: Raw value from JSON

    Returns:
        Converted value
    """
    origin_type = _get_origin_type(field_type)

    # Handle json_repair converting None to 'None'
    if value == "None":
        if origin_type is not str:
            return None

    if origin_type is str:
        # Convert any type to string
        return _any_to_str(value)
    elif origin_type is int:
        if isinstance(value, str):
            if value is None or value == "None":
                return 0
            try:
                return int(value)
            except (ValueError, TypeError):
                pass
    elif origin_type is float:
        if isinstance(value, str):
            if value is None or value == "None":
                return 0.0
            try:
                return float(value)
            except (ValueError, TypeError):
                pass
    elif origin_type is list:
        if isinstance(value, str):
            # Wrap single string in list
            return [value]
        elif isinstance(value, dict):
            # Wrap single dict in list
            return [value]

    return value


def parse_value_with_tolerance(value, annotation):
    """
    Layer 4 & 5: Parse value with tolerance and validation.

    Similar to json_adapter.parse_value().

    Args:
        value: Raw value
        annotation: Target type annotation

    Returns:
        Parsed and validated value

    Raises:
        Exception: If parsing fails even after tolerance attempts
    """
    # Handle None string from json_repair
    if annotation is str or annotation is Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        else:
            return (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value)
            )

    if value == "None":
        return None

    # Apply value fault tolerance (inline for efficiency)
    origin_type = _get_origin_type(annotation)
    if origin_type is str:
        parsed_value = _any_to_str(value)
    elif origin_type is int:
        if isinstance(value, str):
            if value == "None":
                parsed_value = 0
            else:
                try:
                    parsed_value = int(value)
                except (ValueError, TypeError):
                    parsed_value = value
        else:
            parsed_value = value
    elif origin_type is float:
        if isinstance(value, str):
            if value == "None":
                parsed_value = 0.0
            else:
                try:
                    parsed_value = float(value)
                except (ValueError, TypeError):
                    parsed_value = value
        else:
            parsed_value = value
    elif origin_type is list:
        if isinstance(value, str):
            parsed_value = [value]
        elif isinstance(value, dict):
            parsed_value = [value]
        else:
            parsed_value = value
    else:
        parsed_value = value

    # Try validation with TypeAdapter
    try:
        return TypeAdapter(annotation).validate_python(parsed_value, strict=False)
    except Exception as e:
        logger.warning(f"TypeAdapter validation failed: {e}")

        # For list types, try filtering invalid items
        if get_origin(annotation) is list and isinstance(parsed_value, list):
            filtered_items = []
            item_type = _get_arg_type(annotation)
            if item_type is not None:
                for item in parsed_value:
                    try:
                        validated_item = TypeAdapter(item_type).validate_python(item, strict=False)
                        filtered_items.append(validated_item)
                    except Exception:
                        logger.warning(f"Skipping invalid list item: {item}")
                        continue

            if filtered_items:
                return filtered_items
            else:
                logger.warning("All list items were filtered out, returning empty list")
                return []

        # Re-raise for non-list types
        raise e


def parse_json_with_stability(
    content: str,
    model_class: Optional[Type] = None,
    expected_fields: Optional[List[str]] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """
    Five-layer JSON parsing with maximum stability.

    Layer 1: Extract JSON content (remove both leading and trailing non-JSON)
    Layer 2: Repair JSON with json_repair
    Layer 3: Structure tolerance (list→object, extra fields filtering)
    Layer 4: Value fault tolerance (type conversion)
    Layer 5: Validation tolerance (strict=False + list item filtering)

    Args:
        content: Raw LLM response string
        model_class: Optional Pydantic model class to validate against
        expected_fields: Optional list of field names to keep (filter out extra fields)

    Returns:
        Tuple of (parsed_data, error_message). error_message is None on success.
    """
    if not content:
        return None, "Empty content"

    # Layer 1: Extract JSON content (both leading and trailing)
    try:
        cleaned_content = extract_json_content(content)
        if not cleaned_content:
            return None, "No JSON content found after cleanup"
    except Exception as e:
        logger.warning(f"Layer 1 cleanup failed: {e}")
        cleaned_content = content

    # Layer 2: Parse with json_repair
    parsed_data = None
    try:
        parsed_data = json_repair.loads(cleaned_content)
    except Exception as e:
        logger.warning(f"Layer 2 json_repair failed: {e}")
        # Fallback: try regular json.loads
        try:
            parsed_data = json.loads(cleaned_content)
        except Exception as e2:
            return None, f"JSON parsing failed: {e} (fallback also failed: {e2})"

    # Layer 3: Structure tolerance
    # Handle case where model returns [{"xxx": ...}] instead of {"xxx": ...}
    if isinstance(parsed_data, list) and len(parsed_data) > 0:
        parsed_data = parsed_data[0]
        logger.info("Extracted first item from list response")

    if not isinstance(parsed_data, dict):
        return None, f"Expected dict after parsing, got {type(parsed_data)}"

    # Filter to only expected fields if provided
    if expected_fields:
        filtered_data = {}
        for k, v in parsed_data.items():
            if k in expected_fields:
                filtered_data[k] = v
        parsed_data = filtered_data

    # If no model class, return the raw dict
    if model_class is None:
        return parsed_data, None

    # Layer 4 & 5: Validate with model
    try:
        # First try direct model validation
        return model_class.model_validate(parsed_data), None
    except Exception as e:
        logger.warning(f"Direct model validation failed, trying parse_value_with_tolerance: {e}")
        logger.warning(f"content={content}")
        # Fallback: Apply value fault tolerance to each field individually
        try:
            field_types = get_type_hints(model_class)
            tolerant_data = {}
            for field_name, field_value in parsed_data.items():
                if field_name in field_types:
                    try:
                        tolerant_data[field_name] = parse_value_with_tolerance(
                            field_value, field_types[field_name]
                        )
                    except Exception as field_e:
                        logger.warning(f"Field {field_name} parsing failed: {field_e}")
                        # Skip this field rather than failing the whole parse
                        continue

            # Now try validating with the tolerant data
            return model_class.model_validate(tolerant_data), None
        except Exception as e2:
            return None, f"Model validation failed even after tolerance: {e} (fallback: {e2})"


