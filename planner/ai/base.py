"""What any AI client must provide. Tests swap in fake clients; no API key needed."""

from abc import ABC, abstractmethod


class AIRateLimited(Exception):
    """The API said 429 / RESOURCE_EXHAUSTED. retry_after_s is the delay it asked for, if any."""

    def __init__(self, message: str = "rate limited", retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class AIServerBusy(Exception):
    """The API is temporarily overloaded or erroring (500 / 503 / 504). Worth retrying after a short wait."""


class AIClient(ABC):
    model: str = "unknown"
    uses_quota: bool = True          # False for the mock: its calls don't count toward the daily cap

    @abstractmethod
    def generate(self, packs: list[dict]) -> str:
        """Send one batch of context packs; return the raw JSON text (an array, one object per SKU)."""
