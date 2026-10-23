"""The shape every AI answer must have. Anything else is treated as invalid output."""

import re
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


class AISuggestion(BaseModel):
    sku_id: str
    likely_cause: str
    suggested_qty_min: int
    suggested_qty_max: int
    confidence: Literal["low", "medium", "high"]
    reasoning: str
    questions_for_raghav: list[str] = []

    @field_validator("reasoning")
    @classmethod
    def at_most_three_sentences(cls, text: str) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        return " ".join(sentences[:3])

    @model_validator(mode="after")
    def min_not_above_max(self):
        if self.suggested_qty_min > self.suggested_qty_max:
            self.suggested_qty_min, self.suggested_qty_max = self.suggested_qty_max, self.suggested_qty_min
        return self
