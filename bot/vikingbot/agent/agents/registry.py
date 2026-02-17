"""
Agent registry for managing specialized agents.

This module provides a registry system to:
- Register new agents
- Retrieve agents by name
- List all available agents
"""

from typing import Any, Callable

from loguru import logger

from vikingbot.agent.agents.base import AgentConfig
from vikingbot.agent.agents.explore import create_explore_agent
from vikingbot.agent.agents.librarian import create_librarian_agent

# Type alias for agent factory functions
AgentFactory = Callable[[str | None], AgentConfig]

# Global registry
_registry: dict[str, AgentFactory] = {}


class AgentRegistry:
    """
    Registry for managing specialized agents.

    Provides methods to register, retrieve, and list available agents.
    """

    def __init__(self):
        self._agents: dict[str, AgentFactory] = {}
        self._register_default_agents()

    def _register_default_agents(self) -> None:
        """Register the built-in default agents."""
        self.register("explore", create_explore_agent)
        self.register("librarian", create_librarian_agent)

    def register(self, name: str, factory: AgentFactory) -> None:
        """
        Register a new agent factory.

        Args:
            name: Unique name for the agent
            factory: Function that creates an AgentConfig

        Raises:
            ValueError: If an agent with this name is already registered
        """
        if name in self._agents:
            raise ValueError(f"Agent with name '{name}' is already registered")

        self._agents[name] = factory
        logger.debug(f"Registered agent: {name}")

    def get(self, name: str, model: str | None = None) -> AgentConfig:
        """
        Get an agent configuration by name.

        Args:
            name: Name of the agent to retrieve
            model: Optional model override for this agent

        Returns:
            AgentConfig for the requested agent

        Raises:
            KeyError: If no agent with this name is registered
        """
        if name not in self._agents:
            raise KeyError(f"No agent registered with name: {name}")

        factory = self._agents[name]
        return factory(model)

    def list(self) -> list[str]:
        """
        List all registered agent names.

        Returns:
            List of agent names
        """
        return sorted(self._agents.keys())

    def has(self, name: str) -> bool:
        """
        Check if an agent is registered.

        Args:
            name: Name to check

        Returns:
            True if agent exists, False otherwise
        """
        return name in self._agents


# Global registry instance
_global_registry = AgentRegistry()


def register_agent(name: str, factory: AgentFactory) -> None:
    """
    Register a new agent factory in the global registry.

    Args:
        name: Unique name for the agent
        factory: Function that creates an AgentConfig

    Raises:
        ValueError: If an agent with this name is already registered
    """
    _global_registry.register(name, factory)


def get_agent(name: str, model: str | None = None) -> AgentConfig:
    """
    Get an agent configuration from the global registry.

    Args:
        name: Name of the agent to retrieve
        model: Optional model override for this agent

    Returns:
        AgentConfig for the requested agent

    Raises:
        KeyError: If no agent with this name is registered
    """
    return _global_registry.get(name, model)


def list_agents() -> list[str]:
    """
    List all registered agent names from the global registry.

    Returns:
        List of agent names
    """
    return _global_registry.list()


def has_agent(name: str) -> bool:
    """
    Check if an agent is registered in the global registry.

    Args:
        name: Name to check

    Returns:
        True if agent exists, False otherwise
    """
    return _global_registry.has(name)
