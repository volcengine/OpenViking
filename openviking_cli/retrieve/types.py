# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Data types for OpenViking retrieval module.
"""

import queue
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ContextType(str, Enum):
    """Context type for retrieval."""

    MEMORY = "memory"
    RESOURCE = "resource"
    SKILL = "skill"


class TraceEventType(str, Enum):
    """Types of trace events for retrieval process visualization."""

    # Recursive search phase
    SEARCH_DIRECTORY_START = "search_directory_start"
    SEARCH_DIRECTORY_RESULT = "search_directory_result"

    # Scoring phase
    EMBEDDING_SCORES = "embedding_scores"
    RERANK_SCORES = "rerank_scores"

    # Selection phase
    CANDIDATE_SELECTED = "candidate_selected"
    CANDIDATE_EXCLUDED = "candidate_excluded"
    DIRECTORY_QUEUED = "directory_queued"

    # Convergence
    CONVERGENCE_CHECK = "convergence_check"
    SEARCH_CONVERGED = "search_converged"

    # Summary
    SEARCH_SUMMARY = "search_summary"


@dataclass
class TraceEvent:
    """
    Single trace event for retrieval process.

    Attributes:
        event_type: Type of event
        timestamp: Relative timestamp in seconds from trace start
        message: Human-readable description
        data: Structured event data for visualization
        query_id: Optional query identifier for multi-query scenarios
    """

    event_type: TraceEventType
    timestamp: float
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    query_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "event_type": self.event_type.value,
            "timestamp": round(self.timestamp, 4),
            "message": self.message,
            "data": self.data,
        }
        if self.query_id:
            result["query_id"] = self.query_id
        return result


@dataclass
class ScoreDistribution:
    """
    Score distribution statistics for visualization.

    Attributes:
        scores: List of (uri, score) tuples sorted by score descending
        min_score: Minimum score
        max_score: Maximum score
        mean_score: Mean score
        threshold: Score threshold used for filtering
    """

    scores: List[tuple]  # [(uri, score), ...]
    min_score: float = 0.0
    max_score: float = 0.0
    mean_score: float = 0.0
    threshold: float = 0.0

    @classmethod
    def from_scores(
        cls,
        uri_scores: List[tuple],
        threshold: float = 0.0,
    ) -> "ScoreDistribution":
        """Create from list of (uri, score) tuples."""
        if not uri_scores:
            return cls(scores=[], threshold=threshold)

        scores_only = [s for _, s in uri_scores]
        return cls(
            scores=sorted(uri_scores, key=lambda x: x[1], reverse=True),
            min_score=min(scores_only),
            max_score=max(scores_only),
            mean_score=sum(scores_only) / len(scores_only),
            threshold=threshold,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "scores": [{"uri": u, "score": round(s, 4)} for u, s in self.scores],
            "min": round(self.min_score, 4),
            "max": round(self.max_score, 4),
            "mean": round(self.mean_score, 4),
            "threshold": self.threshold,
            "count": len(self.scores),
            "above_threshold": sum(1 for _, s in self.scores if s >= self.threshold),
        }


@dataclass
class ThinkingTrace:
    """
    Structured thinking trace for retrieval process visualization.

    Captures the complete retrieval decision process:
    1. Directory location reasoning
    2. Search decisions per directory
    3. Score distributions (embedding + rerank)
    4. Selection/exclusion reasons
    5. Convergence information

    Thread-safe for concurrent query execution using Queue.

    Attributes:
        _events: Queue of trace events (thread-safe)
        start_time: Trace start time (for relative timestamps)
    """

    start_time: float = field(default_factory=time.time)
    _events: queue.Queue = field(default_factory=queue.Queue, init=False, repr=False)

    def add_event(
        self,
        event_type: TraceEventType,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        query_id: Optional[str] = None,
    ) -> None:
        """
        Add a trace event (thread-safe).

        Args:
            event_type: Type of event
            message: Human-readable message
            data: Event data dictionary
            query_id: Optional query identifier for multi-query scenarios
        """
        event = TraceEvent(
            event_type=event_type,
            timestamp=time.time() - self.start_time,
            message=message,
            data=data or {},
            query_id=query_id,
        )
        self._events.put(event)

    def get_events(self, query_id: Optional[str] = None) -> List[TraceEvent]:
        """
        Get all events, optionally filtered by query_id.

        Args:
            query_id: If provided, only return events for this query

        Returns:
            List of trace events (snapshot)
        """
        # Get snapshot of all events
        all_events = list(self._events.queue)

        if query_id is None:
            return all_events
        return [e for e in all_events if e.query_id == query_id]

    @property
    def events(self) -> List[TraceEvent]:
        """Get all events as list."""
        return self.get_events()

    def get_statistics(self) -> Dict[str, Any]:
        """Calculate summary statistics from events."""
        stats = {
            "total_events": len(self.events),
            "duration_seconds": 0.0,
            "directories_searched": 0,
            "candidates_collected": 0,
            "candidates_excluded": 0,
            "convergence_rounds": 0,
        }

        if self.events:
            stats["duration_seconds"] = round(self.events[-1].timestamp, 4)

        for event in self.events:
            if event.event_type == TraceEventType.SEARCH_DIRECTORY_RESULT:
                stats["directories_searched"] += 1
            elif event.event_type == TraceEventType.CANDIDATE_SELECTED:
                stats["candidates_collected"] += event.data.get("count", 1)
            elif event.event_type == TraceEventType.CANDIDATE_EXCLUDED:
                stats["candidates_excluded"] += event.data.get("count", 1)
            elif event.event_type == TraceEventType.CONVERGENCE_CHECK:
                stats["convergence_rounds"] = event.data.get("round", 0)

        return stats

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "events": [e.to_dict() for e in self.events],
            "statistics": self.get_statistics(),
        }

    def to_messages(self) -> List[str]:
        """Convert to simple message list."""
        return [e.message for e in self.events]


@dataclass
class TypedQuery:
    """
    Query targeting a specific context type.

    Attributes:
        query: Query text
        context_type: Target context type (memory/resources/skill)
        intent: Query intent description
        priority: Priority (1-5, 1 is highest)
        target_directories: Directory URIs located by LLM
    """

    query: str
    context_type: ContextType
    intent: str
    priority: int = 3
    target_directories: List[str] = field(default_factory=list)


@dataclass
class QueryPlan:
    """
    Query plan containing multiple TypedQueries.

    Attributes:
        queries: List of typed queries
        session_context: Session context summary
        reasoning: LLM reasoning process
    """

    queries: List[TypedQuery]
    session_context: str
    reasoning: str


@dataclass
class RelatedContext:
    """Related context with summary."""

    uri: str
    abstract: str


@dataclass
class MatchedContext:
    """Matched context from retrieval."""

    uri: str
    context_type: ContextType
    is_leaf: bool = False
    abstract: str = ""
    overview: Optional[str] = None
    category: str = ""
    score: float = 0.0
    match_reason: str = ""

    relations: List[RelatedContext] = field(default_factory=list)


@dataclass
class QueryResult:
    """
    Result for a single TypedQuery.

    Attributes:
        query: Original query
        matched_contexts: List of matched contexts
        searched_directories: Directories that were searched
        thinking_trace: Structured thinking trace for visualization
    """

    query: TypedQuery
    matched_contexts: List[MatchedContext]
    searched_directories: List[str]
    thinking_trace: ThinkingTrace = field(default_factory=ThinkingTrace)

    def get_trace_messages(self) -> List[str]:
        """Get trace as simple message list."""
        return self.thinking_trace.to_messages()


@dataclass
class FindResult:
    """
    Final result from client.search().

    Attributes:
        memories: Matched memory contexts
        resources: Matched resource contexts
        skills: Matched skill contexts
        query_plan: Query plan used
        query_results: Detailed results for each query
        total: Total match count
    """

    memories: List[MatchedContext]
    resources: List[MatchedContext]
    skills: List[MatchedContext]
    query_plan: Optional[QueryPlan] = None
    query_results: Optional[List[QueryResult]] = None
    total: int = 0

    def __iter__(self):
        """Make FindResult iterable by yielding all matched contexts."""
        yield from self.memories
        yield from self.resources
        yield from self.skills

    def __post_init__(self):
        self.total = len(self.memories) + len(self.resources) + len(self.skills)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            "memories": [self._context_to_dict(m) for m in self.memories],
            "resources": [self._context_to_dict(r) for r in self.resources],
            "skills": [self._context_to_dict(s) for s in self.skills],
            "total": self.total,
        }

        if self.query_plan:
            result["query_plan"] = {
                "reasoning": self.query_plan.reasoning,
                "queries": [self._query_to_dict(q) for q in self.query_plan.queries],
            }

        return result

    def _context_to_dict(self, ctx: MatchedContext) -> Dict[str, Any]:
        """Convert MatchedContext to dict."""
        return {
            "context_type": ctx.context_type.value,
            "uri": ctx.uri,
            "is_leaf": ctx.is_leaf,
            "score": ctx.score,
            "category": ctx.category,
            "match_reason": ctx.match_reason,
            "relations": [{"uri": r.uri, "abstract": r.abstract} for r in ctx.relations],
            "abstract": ctx.abstract,
            "overview": ctx.overview,
        }

    def _query_to_dict(self, q: TypedQuery) -> Dict[str, Any]:
        """Convert TypedQuery to dict."""
        return {
            "query": q.query,
            "context_type": q.context_type.value,
            "intent": q.intent,
            "priority": q.priority,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FindResult":
        """Construct FindResult from a dictionary (e.g. HTTP JSON response)."""

        def _parse_context(d: Dict[str, Any]) -> MatchedContext:
            return MatchedContext(
                uri=d.get("uri", ""),
                context_type=ContextType(d.get("context_type", "resource")),
                is_leaf=d.get("is_leaf", False),
                abstract=d.get("abstract", ""),
                overview=d.get("overview"),
                category=d.get("category", ""),
                score=d.get("score", 0.0),
                match_reason=d.get("match_reason", ""),
                relations=[
                    RelatedContext(uri=r.get("uri", ""), abstract=r.get("abstract", ""))
                    for r in d.get("relations", [])
                ],
            )

        return cls(
            memories=[_parse_context(m) for m in data.get("memories", [])],
            resources=[_parse_context(r) for r in data.get("resources", [])],
            skills=[_parse_context(s) for s in data.get("skills", [])],
        )
