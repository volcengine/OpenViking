"""
Base classes and types for specialized agents.

This module defines the core abstractions for Sisyphus-style agents:
- AgentMode: Whether an agent is a primary agent or subagent
- AgentConfig: Configuration for a specialized agent
- AgentPromptMetadata: Metadata about an agent's purpose and usage
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class AgentMode(Enum):
    """
    Agent operating mode.

    Attributes:
        PRIMARY: Full-featured agent that can spawn subagents
        SUBAGENT: Lightweight subagent with limited capabilities
    """

    PRIMARY = "primary"
    SUBAGENT = "subagent"


@dataclass
class AgentPromptMetadata:
    """
    Metadata about an agent's purpose and when to use it.

    Attributes:
        category: High-level category (exploration, consultation, etc.)
        cost: Cost tier (FREE, CHEAP, EXPENSIVE)
        promptAlias: Short alias for the agent
        keyTrigger: Key phrase that triggers this agent
        triggers: List of domain/triggers pairs
        useWhen: When to use this agent
        avoidWhen: When NOT to use this agent
    """

    category: str = "general"
    cost: str = "FREE"
    promptAlias: str = ""
    keyTrigger: str = ""
    triggers: list[dict[str, str]] = field(default_factory=list)
    useWhen: list[str] = field(default_factory=list)
    avoidWhen: list[str] = field(default_factory=list)


@dataclass
class AgentConfig:
    """
    Complete configuration for a specialized agent.

    Attributes:
        description: Human-readable description of the agent
        mode: Operating mode (primary or subagent)
        model: Model to use for this agent
        temperature: Sampling temperature
        prompt: System prompt for the agent
        restrictions: Tool restrictions (which tools are disabled)
    """

    description: str
    mode: AgentMode
    model: str | None = None
    temperature: float = 0.7
    prompt: str = ""
    restrictions: list[str] = field(default_factory=list)


def create_agent_tool_restrictions(disabled_tools: list[str]) -> dict[str, Any]:
    """
    Create tool restrictions configuration.

    Args:
        disabled_tools: List of tool names to disable

    Returns:
        Dictionary with restrictions configuration
    """
    return {
        "restrictions": {
            "disabled_tools": disabled_tools,
        },
    }
