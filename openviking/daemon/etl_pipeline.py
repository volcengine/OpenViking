"""
Batch ETL pipeline for processing conversation events into structured knowledge.
Orchestrates: Filter -> Reconstruct -> Extract -> Deduplicate
"""
import asyncio
from typing import Dict, List

from openviking.daemon.models import ExtractedKnowledge
from openviking.daemon.filters import LowValueFilter
from openviking.daemon.conversation_reconstructor import ConversationReconstructor
from openviking.daemon.knowledge_extractor import KnowledgeExtractor
from openviking.daemon.deduplicator import KnowledgeDeduplicator
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class BatchETLPipeline:
    """Orchestrates the full ETL flow from raw events to structured knowledge."""

    def __init__(self, vlm_config=None):
        self.filter = LowValueFilter()
        self.reconstructor = ConversationReconstructor()
        self.extractor = KnowledgeExtractor(vlm_config=vlm_config)
        self.deduplicator = KnowledgeDeduplicator()

    async def process_batch(self, events: List[Dict]) -> List[ExtractedKnowledge]:
        """
        Process a batch of raw conversation events.

        Flow: events -> filter -> reconstruct -> extract (parallel) -> deduplicate
        """
        logger.info("Processing batch with %d events", len(events))

        # Step 1: Filter low-value content
        filtered_events = self.filter.apply(events)
        logger.info("After filtering: %d events", len(filtered_events))

        if not filtered_events:
            return []

        # Step 2: Reconstruct conversation turns
        turns = self.reconstructor.reconstruct(filtered_events)
        logger.info("Reconstructed %d conversation turns", len(turns))

        if not turns:
            return []

        # Step 3: Extract knowledge in parallel
        tasks = [self.extractor.extract(turn) for turn in turns]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Step 4: Filter errors and deduplicate
        extracted: List[ExtractedKnowledge] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("Extraction failed: %s", result)
                continue

            if result is not None and not self.deduplicator.is_duplicate(result):
                extracted.append(result)

        logger.info("Extracted %d knowledge items from batch", len(extracted))
        return extracted
