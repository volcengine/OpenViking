#!/usr/bin/env python3
"""
Test script to verify the Sisyphus agent architecture implementation.
"""

import sys
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def test_imports():
    """Test that all modules can be imported correctly."""
    print("Testing imports...")

    try:
        from vikingbot.agent.agents import (
            AgentConfig,
            AgentMode,
            AgentRegistry,
            get_agent,
            list_agents,
            register_agent,
            has_agent,
            create_explore_agent,
            EXPLORE_PROMPT_METADATA,
            create_librarian_agent,
            LIBRARIAN_PROMPT_METADATA,
        )
        print("✓ Base agent imports successful")
    except Exception as e:
        print(f"✗ Base agent imports failed: {e}")
        return False

    try:
        from vikingbot.agent.agents.base import (
            AgentConfig,
            AgentMode,
            AgentPromptMetadata,
            create_agent_tool_restrictions,
        )
        print("✓ Agent base imports successful")
    except Exception as e:
        print(f"✗ Agent base imports failed: {e}")
        return False

    try:
        from vikingbot.agent.agents.explore import (
            create_explore_agent,
            EXPLORE_PROMPT_METADATA,
        )
        print("✓ Explore agent imports successful")
    except Exception as e:
        print(f"✗ Explore agent imports failed: {e}")
        return False

    try:
        from vikingbot.agent.agents.registry import (
            AgentRegistry,
            get_agent,
            list_agents,
            register_agent,
            has_agent,
        )
        print("✓ Registry imports successful")
    except Exception as e:
        print(f"✗ Registry imports failed: {e}")
        return False

    try:
        from vikingbot.agent.agents.librarian import (
            create_librarian_agent,
            LIBRARIAN_PROMPT_METADATA,
        )
        print("✓ Librarian agent imports successful")
    except Exception as e:
        print(f"✗ Librarian agent imports failed: {e}")
        return False

    print("\nAll imports successful!")
    return True


def test_agent_registry():
    """Test the agent registry functionality."""
    print("\nTesting agent registry...")

    from vikingbot.agent.agents import (
        AgentRegistry,
        list_agents,
        has_agent,
        get_agent,
    )

    # Test list agents
    agents = list_agents()
    print(f"Registered agents: {agents}")
    if "explore" in agents:
        print("✓ 'explore' agent is registered")
    else:
        print("✗ 'explore' agent not found")
        return False

    if "librarian" in agents:
        print("✓ 'librarian' agent is registered")
    else:
        print("✗ 'librarian' agent not found")
        return False

    # Test has_agent
    if has_agent("explore"):
        print("✓ has_agent('explore') works")
    else:
        print("✗ has_agent('explore') failed")
        return False

    if has_agent("librarian"):
        print("✓ has_agent('librarian') works")
    else:
        print("✗ has_agent('librarian') failed")
        return False

    if not has_agent("nonexistent_agent"):
        print("✓ has_agent('nonexistent_agent') correctly returns False")
    else:
        print("✗ has_agent('nonexistent_agent') failed")
        return False

    # Test get_agent
    try:
        agent_config = get_agent("explore")
        print(f"✓ get_agent('explore') successful")
        print(f"  - Description: {agent_config.description[:60]}...")
        print(f"  - Mode: {agent_config.mode}")
        print(f"  - Temperature: {agent_config.temperature}")
    except Exception as e:
        print(f"✗ get_agent('explore') failed: {e}")
        return False

    print("\nAgent registry tests passed!")
    return True


def test_explore_agent():
    """Test the Explore agent configuration."""
    print("\nTesting Explore agent...")

    from vikingbot.agent.agents import create_explore_agent, EXPLORE_PROMPT_METADATA

    # Test metadata
    print(f"Explore agent category: {EXPLORE_PROMPT_METADATA.category}")
    print(f"Explore agent cost: {EXPLORE_PROMPT_METADATA.cost}")
    print(f"Explore agent useWhen: {len(EXPLORE_PROMPT_METADATA.useWhen)} scenarios")
    print(f"Explore agent avoidWhen: {len(EXPLORE_PROMPT_METADATA.avoidWhen)} scenarios")

    # Test create agent
    agent = create_explore_agent()
    print(f"\nCreated Explore agent:")
    print(f"  - Description length: {len(agent.description)}")
    print(f"  - Mode: {agent.mode}")
    print(f"  - Temperature: {agent.temperature}")
    print(f"  - Prompt length: {len(agent.prompt)}")

    # Check prompt contains key elements
    if "<analysis>" in agent.prompt:
        print("✓ Prompt contains <analysis> tag")
    else:
        print("✗ Prompt missing <analysis> tag")

    if "<results>" in agent.prompt:
        print("✓ Prompt contains <results> tag")
    else:
        print("✗ Prompt missing <results> tag")

    print("\nExplore agent tests passed!")
    return True


def test_librarian_agent():
    """Test the Librarian agent configuration."""
    print("\nTesting Librarian agent...")

    from vikingbot.agent.agents import create_librarian_agent, LIBRARIAN_PROMPT_METADATA

    # Test metadata
    print(f"Librarian agent category: {LIBRARIAN_PROMPT_METADATA.category}")
    print(f"Librarian agent cost: {LIBRARIAN_PROMPT_METADATA.cost}")
    print(f"Librarian agent useWhen: {len(LIBRARIAN_PROMPT_METADATA.useWhen)} scenarios")
    print(f"Librarian agent avoidWhen: {len(LIBRARIAN_PROMPT_METADATA.avoidWhen)} scenarios")

    # Test create agent
    agent = create_librarian_agent()
    print(f"\nCreated Librarian agent:")
    print(f"  - Description length: {len(agent.description)}")
    print(f"  - Mode: {agent.mode}")
    print(f"  - Temperature: {agent.temperature}")
    print(f"  - Prompt length: {len(agent.prompt)}")

    # Check prompt contains key elements
    if "<analysis>" in agent.prompt:
        print("✓ Prompt contains <analysis> tag")
    else:
        print("✗ Prompt missing <analysis> tag")

    if "<sources>" in agent.prompt:
        print("✓ Prompt contains <sources> tag")
    else:
        print("✗ Prompt missing <sources> tag")

    if "<results>" in agent.prompt:
        print("✓ Prompt contains <results> tag")
    else:
        print("✗ Prompt missing <results> tag")

    print("\nLibrarian agent tests passed!")
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing Sisyphus Agent Architecture")
    print("=" * 60)

    results = []

    results.append(("Imports", test_imports()))
    results.append(("Agent Registry", test_agent_registry()))
    results.append(("Explore Agent", test_explore_agent()))
    results.append(("Librarian Agent", test_librarian_agent()))

    print("\n" + "=" * 60)
    print("Summary:")
    print("=" * 60)

    all_passed = True
    for name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{name}: {status}")
        if not passed:
            all_passed = False

    print("=" * 60)

    if all_passed:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
