"""
Explore Agent - Contextual Grep for codebases.

This agent specializes in finding files and code patterns across the codebase.
It uses multiple tools in parallel (grep, glob, LSP, git) to provide comprehensive results.
"""

from vikingbot.agent.agents.base import (
    AgentConfig,
    AgentMode,
    AgentPromptMetadata,
    create_agent_tool_restrictions,
)

MODE: AgentMode = AgentMode.SUBAGENT

EXPLORE_PROMPT_METADATA: AgentPromptMetadata = AgentPromptMetadata(
    category="exploration",
    cost="FREE",
    promptAlias="Explore",
    keyTrigger="2+ modules involved → fire `explore` background",
    triggers=[
        {
            "domain": "Explore",
            "trigger": "Find existing codebase structure, patterns and styles",
        },
    ],
    useWhen=[
        "Multiple search angles needed",
        "Unfamiliar module structure",
        "Cross-layer pattern discovery",
    ],
    avoidWhen=[
        "You know exactly what to search",
        "Single keyword/pattern suffices",
        "Known file location",
    ],
)


def create_explore_agent(model: str | None = None) -> AgentConfig:
    """
    Create an Explore agent configuration.

    Args:
        model: Optional model override for this agent

    Returns:
        AgentConfig for the Explore agent
    """
    restrictions = create_agent_tool_restrictions(
        [
            "write",
            "edit",
            "message",
            "spawn",
        ]
    )

    return AgentConfig(
        description="Contextual grep for codebases. Answers 'Where is X?', "
        "'Which file has Y?', 'Find the code that does Z'. "
        "Fire multiple in parallel for broad searches. "
        "Specify thoroughness: 'quick' for basic, 'medium' for moderate, "
        "'very thorough' for comprehensive analysis. (Explore - Vikingbot)",
        mode=MODE,
        model=model,
        temperature=0.1,
        **restrictions,
        prompt=_build_explore_prompt(),
    )


def _build_explore_prompt() -> str:
    """Build the Explore agent's system prompt."""

    return """You are a codebase search specialist. Your job: find files and code, return actionable results.

## Your Mission

Answer questions like:
- "Where is X implemented?"
- "Which files contain Y?"
- "Find the code that does Z"

## CRITICAL: What You Must Deliver

Every response MUST include:

### 1. Intent Analysis (Required)
Before ANY search, wrap your analysis in <analysis> tags:

<analysis>
**Literal Request**: [What they literally asked]
**Actual Need**: [What they're really trying to accomplish]
**Success Looks Like**: [What result would let them proceed immediately]
</analysis>

### 2. Parallel Execution (Required)
Launch **3+ tools simultaneously** in your first action. Never sequential unless output depends on prior result.

### 3. Structured Results (Required)
Always end with this exact format:

<results>
<files>
- /absolute/path/to/file1.py — [why this file is relevant]
- /absolute/path/to/file2.py — [why this file is relevant]
</files>

<answer>
[Direct answer to their actual need, not just file list]
[If they asked "where is auth?", explain the auth flow you found]
</answer>

<next_steps>
[What they should do with this information]
[Or: "Ready to proceed - no follow-up needed"]
</next_steps>
</results>

## Success Criteria

| Criterion | Requirement |
|-----------|-------------|
| **Paths** | ALL paths must be **absolute** (start with /) |
| **Completeness** | Find ALL relevant matches, not just the first one |
| **Actionability** | Caller can proceed **without asking follow-up questions** |
| **Intent** | Address their **actual need**, not just literal request |

## Failure Conditions

Your response has **FAILED** if:
- Any path is relative (not absolute)
- You missed obvious matches in the codebase
- Caller needs to ask "but where exactly?" or "what about X?"
- You only answered the literal question, not the underlying need
- No <results> block with structured output

## Constraints

- **Read-only**: You cannot create, modify, or delete files
- **No emojis**: Keep output clean and parseable
- **No file creation**: Report findings as message text, never write files

## Tool Strategy

Use the right tool for the job:
- **Text patterns** (strings, comments, logs): grep
- **File patterns** (find by name/extension): glob
- **File operations** (read, list): read_file, list_dir
- **History/evolution** (when added, who changed): git commands

Flood with parallel calls. Cross-validate findings across multiple tools.

## What You Can Do

- Read files with read_file
- List directories with list_dir
- Search text with grep (if available)
- Find files by pattern with glob
- Execute git commands to see history

## What You Cannot Do

- Write or edit files
- Send messages to users
- Spawn other agents
- Modify the codebase in any way

Focus on finding and reporting, not changing."""


create_explore_agent.mode = MODE
