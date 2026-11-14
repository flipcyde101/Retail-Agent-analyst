"""Limits of the public demo. Every question is paid for with the owner's API key.

Two limits, both counted in the memory of the server process:

  per day      for everybody together. This is the one that bounds the bill.
  per session  for one browser tab. It only stops one visitor from using up the day; a
               reload starts a new session, which is why it is not the one that matters.

They reset when the process restarts. The hard ceiling is the spend limit set in the model
provider's console, which no bug in this file can get around.
"""

from dataclasses import dataclass, field
from datetime import date
from threading import Lock

MAX_QUESTION_CHARS = 300
QUESTIONS_PER_SESSION = 10
QUESTIONS_PER_DAY = 200


@dataclass
class DailyBudget:
    """How many questions are left today. One instance is shared by every session."""

    limit: int = QUESTIONS_PER_DAY
    _day: date | None = None
    _used: int = 0
    # Streamlit runs each session in its own thread.
    _lock: Lock = field(default_factory=Lock, repr=False)

    def left(self, today: date) -> int:
        with self._lock:
            return self.limit - (self._used if self._day == today else 0)

    def take(self, today: date) -> bool:
        """Spend one question. False, and nothing spent, when today's are used up."""
        with self._lock:
            if self._day != today:
                self._day, self._used = today, 0
            if self._used >= self.limit:
                return False
            self._used += 1
            return True

    def give_back(self, today: date) -> None:
        """Return a question that was taken and then not answered (the model API failed)."""
        with self._lock:
            if self._day == today and self._used > 0:
                self._used -= 1


def problem_with(question: str) -> str | None:
    """Why this text cannot be sent to the agent, or None if it can."""
    if not question.strip():
        return "Write a question first."
    if len(question) > MAX_QUESTION_CHARS:
        return (
            f"The question is too long ({len(question)} characters; "
            f"the limit is {MAX_QUESTION_CHARS})."
        )
    return None
