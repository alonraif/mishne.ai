"""What models exist, what they cost, and roughly how good they are.

Loaded from `models.json` beside this file, or from the path in
`MISHNE_MODEL_CATALOG`. It is data on purpose.

The evidence for that: building this, I checked the current pricing pages for
all four vendors and **not one** of the model identifiers I would have written
from memory still exists. Not the names, not the prices. A catalog compiled into
the code would have been wrong on the day it shipped and wrong in a way that
fails at runtime with a bad-model-id error.

So: unknown models are allowed. If the operator names a model this file has
never heard of, it runs, and its cost is recorded as unknown rather than as
zero — a missing price must not read as free.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

TIERS = ("fast", "mid", "frontier")


@dataclass(frozen=True)
class Model:
    id: str
    provider: str
    tier: str = "mid"
    price_in: float | None = None
    price_out: float | None = None
    context: int = 128_000

    @property
    def priced(self) -> bool:
        return self.price_in is not None and self.price_out is not None

    @property
    def tier_rank(self) -> int:
        return TIERS.index(self.tier) if self.tier in TIERS else 1

    def cost_for(self, in_tokens: int, out_tokens: int) -> float | None:
        if not self.priced:
            return None
        return (in_tokens * self.price_in
                + out_tokens * self.price_out) / 1_000_000

    def blended_cost(self, in_tokens: int, out_tokens: int) -> float:
        """Cost for ranking. An unpriced model sorts last, never first.

        Treating a missing price as zero would make every unknown model the
        cheapest thing in the catalog and win every cost-policy decision.
        """
        c = self.cost_for(in_tokens, out_tokens)
        return float("inf") if c is None else c


def _path() -> Path:
    override = os.environ.get("MISHNE_MODEL_CATALOG")
    return Path(override) if override else Path(__file__).parent / "models.json"


def load() -> list[Model]:
    path = _path()
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Model(id=m["id"], provider=m["provider"],
                  tier=m.get("tier", "mid"), price_in=m.get("in"),
                  price_out=m.get("out"), context=m.get("context", 128_000))
            for m in raw.get("models", [])]


def verified_on() -> str:
    path = _path()
    if not path.exists():
        return ""
    return json.loads(path.read_text(encoding="utf-8")).get("_verified", "")


def find(model_id: str, provider: str = "") -> Model:
    """A model by id, inventing an unpriced entry when it is not catalogued."""
    for m in load():
        if m.id == model_id and (not provider or m.provider == provider):
            return m
    return Model(id=model_id, provider=provider or "unknown")
