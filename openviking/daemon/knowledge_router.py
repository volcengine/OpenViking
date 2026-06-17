"""
Route extracted knowledge to appropriate viking:// URIs based on category and project.
"""
import re
from typing import Optional

from openviking.daemon.models import ExtractedKnowledge
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class KnowledgeRouter:
    """Routes knowledge items to viking:// URIs based on category."""

    def route(self, knowledge: ExtractedKnowledge) -> Optional[str]:
        """
        Determine the target URI for a knowledge item.

        Routing rules:
        - skills -> viking://skills/<source_tool>/<title>.md
        - memories (with project) -> viking://memories/projects/<project>/decisions.md
        - memories (no project) -> viking://memories/global/<title>.md
        - resources -> viking://resources/<tech_stack>/<title>.md
        """
        category = knowledge.category
        project_name = knowledge.project_name
        title = self._sanitize_filename(knowledge.title)
        source = knowledge.source_tool or "general"

        if category == "skills":
            safe_source = self._sanitize_filename(source)
            return f"viking://skills/{safe_source}/{title}.md"

        elif category == "memories":
            if project_name:
                safe_project = self._sanitize_filename(project_name)
                return f"viking://memories/projects/{safe_project}/decisions.md"
            else:
                return f"viking://memories/global/{title}.md"

        elif category == "resources":
            entity_links = knowledge.entity_links
            tech_stack = self._sanitize_filename(entity_links[0]) if entity_links else "general"
            return f"viking://resources/{tech_stack}/{title}.md"

        else:
            logger.warning("Unknown category: %s", category)
            return None

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Sanitize a string for use as a filename."""
        sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
        sanitized = sanitized.strip().replace(' ', '_')
        return sanitized[:50]
