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

    response = response.strip()

    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", response, re.DOTALL)
    if match:
        json_str = match.group(1).strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", response)
    if match:
        json_str = match.group(0)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    try:
        fixed_response = _fix_json_quotes(response)
        return json.loads(fixed_response)
    except json.JSONDecodeError:
        pass

    try:
        return json_repair.loads(response)
    except (json.JSONDecodeError, ValueError):
        logger.error(f"Failed to parse JSON from response: {response}")

    return None


def _fix_json_quotes(json_str: str) -> str:
    import re

    def fix_quotes_in_match(match):
        key = match.group(1)
        value = match.group(2)
        fixed_value = value.replace('"', '\\"')
        return f'"{key}":"{fixed_value}"'

    pattern = r'"([^"]+)":"([^"]*(?:"[^"]*)*)"'
    try:
        fixed = re.sub(pattern, fix_quotes_in_match, json_str)
        return fixed
    except:
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

    prompt += "\nOnly output JSON, no other text."
    return prompt


class StructuredVLM:
    """
    Wrapper for VLM with structured output support.

    Provides unified interface for getting JSON responses from VLM
    with automatic parsing and validation.
    """

    def __init__(self, vlm_config: Optional[Dict[str, Any]] = None):
        """Initialize structured VLM wrapper.

        Args:
            vlm_config: VLM configuration dict, if None uses default config
        """
        self.vlm_config = vlm_config
        self._vlm_instance = None

    def _get_vlm(self):
        """Get VLM instance."""
        if self._vlm_instance is None:
            from .base import VLMFactory

            config = self.vlm_config or {}
            self._vlm_instance = VLMFactory.create(config)
        return self._vlm_instance

    def complete_json(
        self,
        prompt: str,
        schema: Optional[Dict[str, Any]] = None,
        thinking: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Get JSON completion from VLM."""
        if schema:
            prompt = f"{prompt}\n\n{get_json_schema_prompt(schema)}"

        response = self._get_vlm().get_completion(prompt, thinking)
        return parse_json_from_response(response)

    async def complete_json_async(
        self,
        prompt: str,
        schema: Optional[Dict[str, Any]] = None,
        thinking: bool = False,
        max_retries: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Async version of complete_json."""
        if schema:
            prompt = f"{prompt}\n\n{get_json_schema_prompt(schema)}"

        response = await self._get_vlm().get_completion_async(prompt, thinking, max_retries)
        return parse_json_from_response(response)

    def complete_model(
        self,
        prompt: str,
        model_class: Type[T],
        thinking: bool = False,
    ) -> Optional[T]:
        """Get structured completion validated against a Pydantic model."""
        schema = model_class.model_json_schema()
        response = self.complete_json(prompt, schema=schema, thinking=thinking)
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
        thinking: bool = False,
        max_retries: int = 0,
    ) -> Optional[T]:
        """Async version of complete_model."""
        schema = model_class.model_json_schema()
        response = await self.complete_json_async(
            prompt, schema=schema, thinking=thinking, max_retries=max_retries
        )
        if response is None:
            return None

        try:
            return model_class.model_validate(response)
        except Exception as e:
            logger.warning(f"Model validation failed: {e}")
            return None

    def get_vision_completion(
        self,
        prompt: str,
        images: list,
        thinking: bool = False,
    ) -> str:
        """Get vision completion."""
        return self._get_vlm().get_vision_completion(prompt, images, thinking)

    async def get_vision_completion_async(
        self,
        prompt: str,
        images: list,
        thinking: bool = False,
    ) -> str:
        """Async vision completion."""
        return await self._get_vlm().get_vision_completion_async(prompt, images, thinking)
