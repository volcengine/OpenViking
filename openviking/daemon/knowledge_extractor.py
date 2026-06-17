"""
LLM-based knowledge extraction from conversations.
Uses OpenViking's existing VLM configuration for intelligent filtering and summarization.
"""
from typing import Dict, Optional

from openviking.daemon.models import ConversationTurn, ExtractedKnowledge
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.llm import parse_json_from_response
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

KNOWLEDGE_EXTRACTION_PROMPT = """# Role: OpenViking Automated Context Extraction Expert

# Task:
Analyze the AI-assisted development conversation below and convert it into structured knowledge for viking:// storage.

# Input:
User question: {user_prompt}
AI answer: {assistant_response}

# Filtering rules:
Output <IGNORE> if the conversation is just:
- Minor syntax error fixes (missing semicolons, typos)
- Invalid commands or retry loops
- Pure code formatting or indentation changes
- Simple factual questions ("what is Python")

Extract ONLY when the conversation contains:
- Explicit system configuration decisions
- Root cause analysis for complex bugs
- New architectural rules for the project
- Core development intent
- Reusable skills or best practices

# Output (strict JSON, no markdown code blocks):
{{
  "status": "EXTRACTED" | "IGNORED",
  "category": "skills" | "memories" | "resources",
  "confidence": 0.0-1.0,
  "title": "One-line summary (max 30 chars)",
  "project_name": "project name or null",
  "entity_links": ["tech tags", "module names"],
  "content": "Concise conclusion. What was the problem, what was the solution, why this choice.",
  "actionable_steps": ["steps if skills, else empty"]
}}
"""


class KnowledgeExtractor:
    """Extracts structured knowledge from conversation turns using LLM."""

    def __init__(self, vlm_config=None):
        """
        Args:
            vlm_config: Optional VLMConfig instance. If None, uses OpenViking's global config.
                        Pass a mock for testing.
        """
        self._vlm_config = vlm_config

    def _get_vlm(self):
        """Lazy-load the VLM config from OpenViking if not provided."""
        if self._vlm_config is None:
            self._vlm_config = get_openviking_config().vlm
        return self._vlm_config

    async def extract(self, turn: ConversationTurn) -> Optional[ExtractedKnowledge]:
        """Extract knowledge from a conversation turn. Returns None if not valuable."""
        prompt = KNOWLEDGE_EXTRACTION_PROMPT.format(
            user_prompt=turn.user_prompt,
            assistant_response=turn.assistant_response,
        )

        try:
            response = await self._call_llm(prompt)

            if not response or response.get("status") != "EXTRACTED":
                return None

            if response.get("confidence", 0) < 0.6:
                return None

            return ExtractedKnowledge(
                status=response["status"],
                category=response["category"],
                title=response.get("title", "")[:50],
                content=self._clean_content(response.get("content", "")),
                confidence=response.get("confidence", 0.0),
                project_name=response.get("project_name"),
                entity_links=response.get("entity_links", []),
                actionable_steps=response.get("actionable_steps", []),
                timestamp=turn.timestamp,
                source_tool=turn.source_tool,
            )

        except Exception as e:
            logger.error("Error extracting knowledge: %s", e)
            return None

    async def _call_llm(self, prompt: str) -> Optional[Dict]:
        """Call VLM via get_completion_async and parse JSON response."""
        try:
            vlm = self._get_vlm()
            # get_completion_async returns str when no tools are provided
            raw_response = await vlm.get_completion_async(prompt=prompt)

            # parse_json_from_response handles markdown code block stripping,
            # JSON extraction, and json_repair fallback
            return parse_json_from_response(raw_response)

        except Exception as e:
            logger.error("LLM call failed: %s", e)
            return None

    @staticmethod
    def _clean_content(content: str) -> str:
        """Remove markdown artifacts from content."""
        import re
        content = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
        return content.strip()
