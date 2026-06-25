# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Route extracted knowledge to appropriate viking:// URIs based on category and project.
"""
import hashlib
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

        Routing rules (all under resources/ scope):
        - skills -> viking://resources/skills/<source_tool>/<title>.md
        - memories (with project) -> viking://resources/memories/<project>/decisions.md
        - memories (no project) -> viking://resources/memories/global/<title>.md
        - resources -> viking://resources/<tech_stack>/<title>.md
        """
        category = knowledge.category
        project_name = knowledge.project_name
        title = self._sanitize_filename(knowledge.title)
        source = knowledge.source_tool or "general"

        if category == "skills":
            safe_source = self._sanitize_filename(source)
            return f"viking://resources/skills/{safe_source}/{title}.md"

        elif category == "memories":
            if project_name:
                safe_project = self._sanitize_filename(project_name)
                return f"viking://resources/memories/{safe_project}/decisions.md"
            else:
                return f"viking://resources/memories/global/{title}.md"

        elif category == "resources":
            entity_links = knowledge.entity_links
            tech_stack = self._sanitize_filename(entity_links[0]) if entity_links else "general"
            return f"viking://resources/{tech_stack}/{title}.md"

        else:
            logger.warning("Unknown category: %s", category)
            return None

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Sanitize a string for use as a filename. Non-ASCII names are replaced with a short hash."""
        sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
        sanitized = sanitized.strip().replace(' ', '_')
        sanitized = sanitized[:50]
        # If any non-ASCII characters remain, use a truncated hash instead
        if not sanitized.isascii():
            name_hash = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
            sanitized = name_hash
        return sanitized
