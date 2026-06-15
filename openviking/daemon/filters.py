"""
Rule-based filters for low-value conversations.
Removes noise before LLM processing to save cost and improve quality.
"""
import re
from typing import Dict, List

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class LowValueFilter:
    """Filters out low-value conversations using regex rules."""

    NOISE_PATTERNS = [
        r"^npm\s+(install|update|remove)",
        r"^yarn\s+(add|remove)",
        r"^pip\s+(install|uninstall)",
        r"^git\s+(commit|push|pull|merge)",
        r"^(SyntaxError|TypeError|ImportError|ModuleNotFoundError)",
        r"^Retry\s+\d+/",
        r"^Loading\.+",
        r"^(format|indent|align)\s+(this|the)\s+code",
    ]

    MIN_CONTENT_LENGTH = 20

    def apply(self, events: List[Dict]) -> List[Dict]:
        """Apply filtering rules to a list of events."""
        filtered = []

        for event in events:
            content = event.get("content", "").strip()

            # Rule 1: too short
            if len(content) < self.MIN_CONTENT_LENGTH:
                continue

            # Rule 2: noise pattern match
            if any(re.match(p, content, re.IGNORECASE) for p in self.NOISE_PATTERNS):
                continue

            filtered.append(event)

        logger.debug("Filtered %d events down to %d", len(events), len(filtered))
        return filtered
