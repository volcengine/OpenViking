# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
LLM utilities for OpenViking.

Provides unified structured output handling with response_format support.
"""

import json
import re
from typing import Any, Dict, Optional, Type, TypeVar

import json_repair
from pydantic import BaseModel

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


def parse_json_from_response(response: str) -> Optional[Any]:
    """
    Parse JSON object from LLM text response.

    Handles code blocks and plain JSON strings, including fixing common format issues.

    Args:
        response (str): LLM text response or JSON string

    Returns:
        Optional[Any]: Parsed JSON object, None if parsing fails
    """
    if not isinstance(response, str):
        return None

    # Remove leading and trailing whitespace
    response = response.strip()

    # Strategy 1: Direct parsing
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract JSON from code blocks
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", response, re.DOTALL)
    if match:
        json_str = match.group(1).strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # Strategy 3: Regex match JSON structure
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", response)
    if match:
        json_str = match.group(0)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # Strategy 4: Parse after fixing common issues
    try:
        # Fix internal unescaped quote issues
        fixed_response = _fix_json_quotes(response)
        return json.loads(fixed_response)
    except json.JSONDecodeError:
        pass

    # Strategy 5: Use json_repair library
    try:
        return json_repair.loads(response)
    except (json.JSONDecodeError, ValueError):
        logger.error(f"Failed to parse JSON from response: {response}")

    return None


def _fix_json_quotes(json_str: str) -> str:
    """
    Fix quote issues in JSON string
    """
    # Match unescaped quotes in string values
    # This is a simple fix strategy, may not be perfect but handles common cases

    # First handle obvious unescaped quote issues
    # Example: "title":"Use\"codex\"tool" -> "title":"Use\"codex\"tool"

    import re

    # Find all string values and fix quotes in them
    def fix_quotes_in_match(match):
        key = match.group(1)
        value = match.group(2)
        # Escape quotes in value
        fixed_value = value.replace('"', '\\"')
        return f'"{key}":"{fixed_value}"'

    # Match "key":"value" pattern and fix quotes in value
    pattern = r'"([^"]+)":"([^"]*(?:"[^"]*)*)"'
    try:
        fixed = re.sub(pattern, fix_quotes_in_match, json_str)
        return fixed
    except Exception:
        return json_str


def parse_json_to_model(response: str, model_class: Type[T]) -> Optional[T]:
    """
    Parse JSON response into a Pydantic model.

    Args:
        response: Raw LLM response text
        model_class: Pydantic model class to parse into

    Returns:
        Parsed model instance or None if parsing fails
    """
    data = parse_json_from_response(response)
    if data is None:
        return None

    try:
        return model_class.model_validate(data)
    except Exception as e:
        logger.warning(f"Failed to validate JSON against model {model_class.__name__}: {e}")
        return None


def get_json_schema_prompt(schema: Dict[str, Any], description: str = "") -> str:
    """
    Generate a prompt instruction for JSON output.

    Args:
        schema: JSON schema dict
        description: Optional description of expected output

    Returns:
        Prompt instruction string
    """
    schema_str = json.dumps(schema, ensure_ascii=False, indent=2)

    prompt = f"""Please output the result in JSON format.

Output format requirements:
```json
{schema_str}
```
"""
    if description:
        prompt += f"\n{description}\n"

    prompt += "\nOnly output JSON, no other text description."
    return prompt


class StructuredLLM:
    """
    Wrapper for LLM with structured output support.

    Provides unified interface for getting JSON responses from LLM
    with automatic parsing and validation.
    """

    def __init__(self):
        """Initialize structured LLM wrapper."""
        pass

    def _get_vlm(self):
        """Get VLM singleton."""
        from openviking_cli.utils.config import get_openviking_config

        return get_openviking_config().vlm

    def complete_json(
        self,
        prompt: str,
        schema: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get JSON completion from LLM."""
        if schema:
            prompt = f"{prompt}\n\n{get_json_schema_prompt(schema)}"

        response = self._get_vlm().get_completion(prompt)
        return parse_json_from_response(response)

    async def complete_json_async(
        self,
        prompt: str,
        schema: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Async version of complete_json."""
        if schema:
            prompt = f"{prompt}\n\n{get_json_schema_prompt(schema)}"

        response = await self._get_vlm().get_completion_async(prompt)
        return parse_json_from_response(response)

    def complete_model(
        self,
        prompt: str,
        model_class: Type[T],
    ) -> Optional[T]:
        """Get structured completion validated against a Pydantic model."""
        schema = model_class.model_json_schema()
        response = self.complete_json(prompt, schema=schema)
        if response is None:
            return None

        try:
            return model_class.model_validate(response)
        except Exception as e:
            logger.warning(f"Model validation failed: {e}")
            return None

    async def complete_model_async(
        self,
        prompt: str,
        model_class: Type[T],
    ) -> Optional[T]:
        """Async version of complete_model."""
        schema = model_class.model_json_schema()
        response = await self.complete_json_async(prompt, schema=schema)
        if response is None:
            return None

        try:
            return model_class.model_validate(response)
        except Exception as e:
            logger.warning(f"Model validation failed: {e}")
            return None
