"""Multi-turn conversation manager for the analytics pipeline."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversationTurn:
    question: str
    sql: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""


class ConversationManager:
    """Stores conversation history and detects follow-up questions."""

    _FOLLOWUP_PATTERNS = re.compile(
        r"\b(what about|how about|now sort|instead|and for|the same|"
        r"those|them|that|this|it|its|they|their|he|she|his|her|"
        r"can you explain|break.?down|more detail|specifically)\b",
        re.IGNORECASE,
    )

    def __init__(self, max_turns: int = 10) -> None:
        self.history: list[ConversationTurn] = []
        self.max_turns = max_turns

    def add_turn(self, turn: ConversationTurn) -> None:
        self.history.append(turn)
        if len(self.history) > self.max_turns:
            self.history = self.history[-self.max_turns:]

    def is_followup(self, question: str) -> bool:
        if not self.history:
            return False
        q = question.strip()
        if len(q.split()) <= 6:
            return True
        if self._FOLLOWUP_PATTERNS.search(q):
            return True
        return False

    def build_context_prompt(self, question: str) -> str:
        """Build a context-enriched prompt for follow-up questions."""
        if not self.history:
            return question

        last = self.history[-1]
        parts = [f"Previous question: {last.question}"]
        if last.sql:
            parts.append(f"Previous SQL: {last.sql}")
        if last.answer:
            parts.append(f"Previous answer: {last.answer[:300]}")
        parts.append(f"Follow-up question: {question}")
        parts.append("Generate SQL for the follow-up question, modifying the previous query as needed.")
        return "\n".join(parts)

    def clear(self) -> None:
        self.history.clear()
