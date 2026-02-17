"""
Specialized agents for Sisyphus architecture.

This module contains expert agents that handle specific domains:
- Oracle: Architecture and debugging consultation
- Librarian: External documentation and codebase research
- Explore: Fast contextual codebase search
"""

from vikingbot.agent.agents.base import AgentConfig, AgentMode
from vikingbot.agent.agents.registry import (
    AgentRegistry,
    get_agent,
    list_agents,
    register_agent,
    has_agent,
)
from vikingbot.agent.agents.explore import create_explore_agent, EXPLORE_PROMPT_METADATA
from vikingbot.agent.agents.librarian import create_librarian_agent, LIBRARIAN_PROMPT_METADATA

__all__ = [
    # Base
    "AgentConfig",
    "AgentMode",
    # Registry
    "AgentRegistry",
    "get_agent",
    "list_agents",
    "register_agent",
    "has_agent",
    # Explore Agent
    "create_explore_agent",
    "EXPLORE_PROMPT_METADATA",
    # Librarian Agent
    "create_librarian_agent",
    "LIBRARIAN_PROMPT_METADATA",
]
