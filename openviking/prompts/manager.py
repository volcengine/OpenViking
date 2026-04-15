# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Prompt template management for OpenViking."""

import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from jinja2 import Template
from pydantic import BaseModel, Field

from openviking_cli.utils.config import (
    OPENVIKING_PROMPT_TEMPLATES_DIR_ENV,
    get_openviking_config,
)


class PromptMetadata(BaseModel):
    """Metadata for a prompt template."""

    id: str
    name: str
    description: str
    version: str
    language: str
    category: str


class PromptVariable(BaseModel):
    """Variable definition for a prompt template."""

    name: str
    type: str
    description: str
    default: Any = None
    required: bool = True
    max_length: Optional[int] = None


class PromptTemplate(BaseModel):
    """Complete prompt template definition."""

    metadata: PromptMetadata
    variables: List[PromptVariable] = Field(default_factory=list)
    template: str
    output_schema: Optional[Dict[str, Any]] = None
    llm_config: Optional[Dict[str, Any]] = None


class PromptManager:
    """
    Manages prompt templates with caching and variable interpolation.

    Features:
    - Load prompts from YAML files
    - Cache loaded prompts for performance
    - Validate variables before rendering
    - Thread-safe caching
    """

    def __init__(
        self,
        templates_dir: Optional[Path] = None,
        enable_caching: bool = True,
    ):
        """
        Initialize prompt manager.

        Args:
            templates_dir: Directory containing YAML templates.
                          If None, uses bundled templates.
            enable_caching: Enable prompt template caching
        """
        self.templates_dir = self._resolve_templates_dir(templates_dir)
        self.enable_caching = enable_caching
        self._cache: Dict[str, PromptTemplate] = {}
        self._lock = threading.RLock()

    @classmethod
    def _resolve_templates_dir(cls, templates_dir: Optional[Path]) -> Path:
        """Resolve prompt templates directory with runtime overrides."""
        if templates_dir is not None:
            return Path(templates_dir)

        env_dir = os.environ.get(OPENVIKING_PROMPT_TEMPLATES_DIR_ENV)
        if env_dir:
            return Path(env_dir).expanduser()

        try:
            config = get_openviking_config()
        except FileNotFoundError:
            return cls._get_bundled_templates_dir()

        config_dir = config.prompts.templates_dir.strip()
        if config_dir:
            return Path(config_dir).expanduser()

        return cls._get_bundled_templates_dir()

    @staticmethod
    def _get_bundled_templates_dir() -> Path:
        """Get path to bundled prompt templates."""
        return Path(__file__).parent / "templates"

    def load_template(self, prompt_id: str) -> PromptTemplate:
        """
        Load a prompt template by ID.

        Args:
            prompt_id: Prompt identifier (e.g., "vision.image_understanding")

        Returns:
            PromptTemplate instance

        Raises:
            FileNotFoundError: If template file not found
            ValidationError: If YAML is invalid
        """
        # Check cache
        if self.enable_caching and prompt_id in self._cache:
            return self._cache[prompt_id]

        # Load from YAML file
        file_path = self._resolve_template_path(prompt_id)
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        template = PromptTemplate.model_validate(data)

        # Cache if enabled
        if self.enable_caching:
            with self._lock:
                self._cache[prompt_id] = template

        return template

    def _resolve_template_path(self, prompt_id: str) -> Path:
        """
        Resolve prompt ID to file path.

        Examples:
            "vision.image_understanding" -> "vision/image_understanding.yaml"
            "compression.summary" -> "compression/summary.yaml"
        """
        parts = prompt_id.split(".")
        category = parts[0]
        name = "_".join(parts[1:])
        relative_path = Path(category) / f"{name}.yaml"

        primary_path = self.templates_dir / relative_path
        if primary_path.exists():
            return primary_path

        bundled_path = self._get_bundled_templates_dir() / relative_path
        if bundled_path.exists():
            return bundled_path

        return primary_path

    def render(
        self,
        prompt_id: str,
        variables: Optional[Dict[str, Any]] = None,
        validate: bool = True,
    ) -> str:
        """
        Render a prompt template with variable substitution.

        Args:
            prompt_id: Prompt identifier
            variables: Variables to substitute {var_name: value}
            validate: Validate variables before rendering

        Returns:
            Rendered prompt string

        Raises:
            ValueError: If required variables are missing or invalid
        """
        template = self.load_template(prompt_id)
        variables = variables or {}

        # Apply defaults
        for var_def in template.variables:
            if var_def.name not in variables and var_def.default is not None:
                variables[var_def.name] = var_def.default

        # Validate variables
        if validate:
            self._validate_variables(template, variables)

        # Truncate string variables to max_length
        for var_def in template.variables:
            if (
                var_def.max_length
                and var_def.name in variables
                and isinstance(variables[var_def.name], str)
            ):
                variables[var_def.name] = variables[var_def.name][: var_def.max_length]

        # Render template with Jinja2
        jinja_template = Template(template.template)
        return jinja_template.render(**variables)

    def _validate_variables(self, template: PromptTemplate, variables: Dict[str, Any]) -> None:
        """Validate provided variables against template requirements."""
        # Check required variables
        for var_def in template.variables:
            if var_def.required and var_def.name not in variables:
                raise ValueError(
                    f"Required variable '{var_def.name}' not provided for "
                    f"prompt '{template.metadata.id}'"
                )

        # Type validation (basic)
        for var_def in template.variables:
            if var_def.name in variables:
                value = variables[var_def.name]
                expected_type = {
                    "string": str,
                    "int": int,
                    "float": (int, float),
                    "bool": bool,
                }.get(var_def.type)

                if expected_type and not isinstance(value, expected_type):
                    raise ValueError(
                        f"Variable '{var_def.name}' expects type {var_def.type}, "
                        f"got {type(value).__name__}"
                    )

    def get_llm_config(self, prompt_id: str) -> Dict[str, Any]:
        """Get LLM configuration for a prompt."""
        template = self.load_template(prompt_id)
        return template.llm_config or {}

    def list_prompts(self, category: Optional[str] = None) -> List[str]:
        """
        List available prompt IDs.

        Args:
            category: Filter by category (e.g., "vision")

        Returns:
            List of prompt IDs
        """
        prompts = []
        for yaml_file in self.templates_dir.rglob("*.yaml"):
            rel_path = yaml_file.relative_to(self.templates_dir)
            category_name = rel_path.parent.name
            file_stem = yaml_file.stem
            prompt_id = f"{category_name}.{file_stem}"

            if category is None or category_name == category:
                prompts.append(prompt_id)

        return sorted(prompts)

    def clear_cache(self) -> None:
        """Clear the prompt template cache."""
        with self._lock:
            self._cache.clear()


# Global singleton instance (similar to parser/registry.py pattern)
_default_manager: Optional[PromptManager] = None


def get_manager() -> PromptManager:
    """Get global PromptManager singleton."""
    global _default_manager
    if _default_manager is None:
        _default_manager = PromptManager()
    return _default_manager


# Convenience functions: wrap singleton access
def render_prompt(prompt_id: str, variables: Optional[Dict[str, Any]] = None) -> str:
    """
    Render a prompt using the global singleton.

    Args:
        prompt_id: Prompt identifier (e.g., "vision.image_understanding")
        variables: Variables for substitution

    Returns:
        Rendered prompt string
    """
    return get_manager().render(prompt_id, variables)


def get_llm_config(prompt_id: str) -> Dict[str, Any]:
    """
    Get LLM configuration for a prompt using the global singleton.

    Args:
        prompt_id: Prompt identifier

    Returns:
        LLM configuration dictionary
    """
    return get_manager().get_llm_config(prompt_id)
