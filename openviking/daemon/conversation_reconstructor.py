"""
Reconstruct conversation turns from flat event lists.
Pairs user prompts with assistant responses into structured ConversationTurn objects.
"""
from typing import Dict, List

from openviking.daemon.models import ConversationTurn
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class ConversationReconstructor:
    """Reconstructs paired conversation turns from chronological events."""

    def reconstruct(self, events: List[Dict]) -> List[ConversationTurn]:
        """
        Pair user prompts with assistant responses.
        Events are sorted by timestamp. An assistant response is paired
        with the most recent unpaired user prompt.
        """
        turns: List[ConversationTurn] = []
        current_user_prompt = None
        current_metadata: Dict = {}

        sorted_events = sorted(events, key=lambda e: e.get("timestamp", ""))

        for event in sorted_events:
            role = event.get("role")
            content = event.get("content", "")

            if role == "user":
                current_user_prompt = content
                current_metadata = {
                    "session_id": event.get("session_id"),
                    "project_name": event.get("project_name"),
                    "timestamp": event.get("timestamp"),
                    "source_tool": event.get("tool_name"),
                }
            elif role == "assistant" and current_user_prompt:
                turns.append(
                    ConversationTurn(
                        user_prompt=current_user_prompt,
                        assistant_response=content,
                        session_id=current_metadata.get("session_id"),
                        project_name=current_metadata.get("project_name"),
                        timestamp=current_metadata.get("timestamp"),
                        source_tool=current_metadata.get("source_tool"),
                    )
                )
                current_user_prompt = None
                current_metadata = {}

        logger.info("Reconstructed %d conversation turns from %d events", len(turns), len(events))
        return turns
