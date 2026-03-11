"""Multi-turn conversation support for the analytics pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_FOLLOWUP_PATTERNS = re.compile(
    r"\b(what about|how about|now sort|now filter|now show|instead|"
    r"same but|and also|can you also|break it down|drill down|"
    r"sort by|group by|order by|filter by|limit to|exclude|"
    r"explain|why|more detail)\b",
    re.IGNORECASE,
)

_PRONOUN_START = re.compile(
    r"^(it|that|they|those|them|this|these|the same|its)\b",
    re.IGNORECASE,
)

_CONJUNCTION_START = re.compile(
    r"^(and|but|or|also|now)\b",
    re.IGNORECASE,
)

_MAX_HISTORY_TURNS = 3


@dataclass
class Turn:
    question: str
    sql: str | None
    answer: str


class ConversationManager:
    """Stores conversation history and enriches follow-up questions with context."""

    def __init__(self) -> None:
        self._turns: list[Turn] = []

    @property
    def turns(self) -> list[Turn]:
        return list(self._turns)

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    def add_turn(self, question: str, sql: str | None, answer: str) -> None:
        self._turns.append(Turn(question=question, sql=sql, answer=answer))

    def clear(self) -> None:
        self._turns.clear()

    def is_followup(self, question: str) -> bool:
        if not self._turns:
            return False

        q = question.strip()
        if not q:
            return False

        if _PRONOUN_START.search(q):
            return True
        if _CONJUNCTION_START.search(q):
            return True
        if _FOLLOWUP_PATTERNS.search(q):
            return True

        return False

    def build_context_prompt(self, question: str) -> str:
        """Return the question enriched with conversation history if it's a follow-up."""
        if not self.is_followup(question):
            return question

        recent = self._turns[-_MAX_HISTORY_TURNS:]
        parts: list[str] = ["Conversation history:"]
        for i, turn in enumerate(recent, 1):
            parts.append(f"Q{i}: {turn.question}")
            if turn.sql:
                parts.append(f"SQL{i}: {turn.sql}")
            if turn.answer:
                parts.append(f"A{i}: {turn.answer}")
        parts.append(f"Follow-up question: {question}")
        parts.append("Rewrite and answer the follow-up as a standalone query.")
        return "\n".join(parts)
