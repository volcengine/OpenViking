# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Path Variable System for OpenViking.

Provides URI template resolution with support for variables like {calendar:today}.
"""

import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, Optional, Set

from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# Regex to match variables: {namespace:key}
VARIABLE_PATTERN = re.compile(r"\{([a-zA-Z0-9_]+):([a-zA-Z0-9_]+)\}")


class VariableProvider(ABC):
    """Base class for variable providers."""

    @property
    @abstractmethod
    def namespace(self) -> str:
        """Get the namespace for this provider."""
        pass

    @abstractmethod
    def get_variables(self) -> Dict[str, str]:
        """Get all variables provided by this provider."""
        pass

    def resolve(self, key: str) -> Optional[str]:
        """Resolve a variable key to its value."""
        variables = self.get_variables()
        return variables.get(key)


class CalendarVariableProvider(VariableProvider):
    """
    Calendar-based variable provider.

    Variables:
        {calendar:today}      - Full date path: 2026/05/07
        {calendar:yesterday}  - Yesterday's date path: 2026/05/06
        {calendar:tomorrow}   - Tomorrow's date path: 2026/05/08
        {calendar:year}       - Year: 2026
        {calendar:month}      - Month with leading zero: 05
        {calendar:day}        - Day with leading zero: 07
        {calendar:ym}         - Year/month: 2026/05
        {calendar:quarter}    - Quarter: Q1, Q2, Q3, Q4
        {calendar:yq}         - Year/quarter: 2026/Q2
        {calendar:week}       - ISO week number with leading zero: 18
        {calendar:yw}         - Year/ISO week: 2026/w18
    """

    @property
    def namespace(self) -> str:
        return "calendar"

    def __init__(self, dt: Optional[datetime] = None):
        self.dt = dt

    def get_variables(self) -> Dict[str, str]:
        dt = self.dt or datetime.now()
        year = dt.year
        month = f"{dt.month:02d}"
        day = f"{dt.day:02d}"

        # Quarter calculation
        quarter = f"Q{(dt.month - 1) // 3 + 1}"

        # ISO week number
        iso_year, iso_week, _ = dt.isocalendar()
        week = f"{iso_week:02d}"

        # Helper to format date path
        def format_date_path(target_dt: datetime) -> str:
            return f"{target_dt.year}/{target_dt.month:02d}/{target_dt.day:02d}"

        yesterday = dt - timedelta(days=1)
        tomorrow = dt + timedelta(days=1)

        return {
            "today": format_date_path(dt),
            "yesterday": format_date_path(yesterday),
            "tomorrow": format_date_path(tomorrow),
            "year": str(year),
            "month": month,
            "day": day,
            "ym": f"{year}/{month}",
            "quarter": quarter,
            "yq": f"{year}/{quarter}",
            "week": week,
            "yw": f"{iso_year}/w{week}",
        }


class PathVariableResolver:
    """
    Resolves URI templates with path variables.

    Example:
        resolver = PathVariableResolver()
        resolved = resolver.resolve("viking://resources/emails/{calendar:today}/inbox")
        # Returns: "viking://resources/emails/2026/05/07/inbox"
    """

    def __init__(self, extra_providers: Optional[Dict[str, VariableProvider]] = None):
        self._providers: Dict[str, VariableProvider] = {}

        # Register default providers
        self._register_provider(CalendarVariableProvider())

        # Register extra providers
        if extra_providers:
            for _, provider in extra_providers.items():
                self._register_provider(provider)

    def _register_provider(self, provider: VariableProvider) -> None:
        """Register a variable provider."""
        if provider.namespace in self._providers:
            logger.warning(
                f"Provider for namespace '{provider.namespace}' already exists, overwriting"
            )
        self._providers[provider.namespace] = provider

    def has_variables(self, uri_template: str) -> bool:
        """Check if a URI template contains variables."""
        return VARIABLE_PATTERN.search(uri_template) is not None

    def extract_variables(self, uri_template: str) -> Set[str]:
        """Extract all variables from a URI template."""
        variables = set()
        matches = VARIABLE_PATTERN.findall(uri_template)
        for namespace, key in matches:
            variables.add(f"{namespace}:{key}")
        return variables

    def resolve(self, uri_template: str, dt: Optional[datetime] = None) -> str:
        """
        Resolve variables in a URI template.

        Args:
            uri_template: URI template with variables (e.g., "viking://resources/{calendar:ym}/logs")
            dt: Optional datetime to use for calendar variables (default: now)

        Returns:
            Resolved URI with variables replaced

        Raises:
            ValueError: If a variable cannot be resolved
        """
        if not self.has_variables(uri_template):
            return uri_template

        # If datetime is provided, create a resolver with that specific time
        if dt is not None:
            # Create a temporary resolver with the custom datetime
            calendar_provider = CalendarVariableProvider(dt)
            temp_providers = dict(self._providers)
            temp_providers["calendar"] = calendar_provider
            temp_resolver = PathVariableResolver(temp_providers)
            return temp_resolver.resolve(uri_template)

        result = uri_template
        unresolved = set()

        # Find all variables
        matches = list(VARIABLE_PATTERN.finditer(uri_template))

        for match in matches:
            full_match = match.group(0)
            namespace = match.group(1)
            key = match.group(2)

            provider = self._providers.get(namespace)
            if not provider:
                unresolved.add(full_match)
                continue

            value = provider.resolve(key)
            if value is None:
                unresolved.add(full_match)
                continue

            # Replace the variable in the result
            result = result.replace(full_match, value)

        if unresolved:
            raise ValueError(
                f"Cannot resolve variables in URI: {', '.join(sorted(unresolved))}. "
                f"URI template: {uri_template}"
            )

        return result


# Global resolver instance
_default_resolver: Optional[PathVariableResolver] = None


def get_path_variable_resolver() -> PathVariableResolver:
    """Get the global path variable resolver instance."""
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = PathVariableResolver()
    return _default_resolver


def resolve_path_variables(uri_template: str, dt: Optional[datetime] = None) -> str:
    """
    Convenience function to resolve path variables in a URI template.

    Args:
        uri_template: URI template with variables
        dt: Optional datetime to use for calendar variables

    Returns:
        Resolved URI
    """
    resolver = get_path_variable_resolver()
    return resolver.resolve(uri_template, dt=dt)


def has_path_variables(uri_template: str) -> bool:
    """
    Convenience function to check if a URI template contains variables.

    Args:
        uri_template: URI template to check

    Returns:
        True if the template contains variables
    """
    resolver = get_path_variable_resolver()
    return resolver.has_variables(uri_template)
