#!/usr/bin/env python3
"""
Chat - Multi-turn conversation interface for OpenViking
"""
from typing import List, Dict, Any
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()


class ChatSession:
    """Manages in-memory conversation history"""

    def __init__(self):
        """Initialize empty conversation history"""
        self.history: List[Dict[str, Any]] = []

    def add_turn(self, question: str, answer: str, sources: List[Dict[str, Any]]) -> None:
        """
        Add a Q&A turn to history

        Args:
            question: User's question
            answer: Assistant's answer
            sources: List of source documents used
        """
        self.history.append({
            'question': question,
            'answer': answer,
            'sources': sources,
            'turn': len(self.history) + 1
        })

    def clear(self) -> None:
        """Clear all conversation history"""
        self.history.clear()

    def get_turn_count(self) -> int:
        """Get number of turns in conversation"""
        return len(self.history)
