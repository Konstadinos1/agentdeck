"""Cost estimation from user-supplied per-model prices.

No prices ship with agentdeck: model prices change often and a stale built-in
table would show confidently wrong numbers. Users copy pricing.example.toml to
~/.config/agentdeck/pricing.toml and fill in current rates from the provider.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from agentdeck.models import AgentSession, TokenUsage

logger = logging.getLogger(__name__)

DEFAULT_PRICING_PATH = Path.home() / ".config" / "agentdeck" / "pricing.toml"
TOKENS_PER_PRICE_UNIT = 1_000_000


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0


class PriceBook:
    def __init__(self, prices: dict[str, ModelPrice] | None = None) -> None:
        self._prices = {key.lower(): price for key, price in (prices or {}).items()}

    def __len__(self) -> int:
        return len(self._prices)

    def lookup(self, model: str) -> ModelPrice | None:
        """Exact match first, then the longest configured prefix (e.g. 'claude-opus-5')."""
        normalized = model.lower()
        if normalized in self._prices:
            return self._prices[normalized]
        prefixes = [key for key in self._prices if normalized.startswith(key)]
        if not prefixes:
            return None
        return self._prices[max(prefixes, key=len)]

    def estimate_usd(self, model: str, usage: TokenUsage) -> float | None:
        price = self.lookup(model)
        if price is None:
            return None
        weighted = (
            usage.input_tokens * price.input
            + usage.output_tokens * price.output
            + usage.cache_read_tokens * price.cache_read
            + usage.cache_write_tokens * price.cache_write
        )
        return weighted / TOKENS_PER_PRICE_UNIT

    def session_cost_usd(self, session: AgentSession) -> float | None:
        if session.reported_cost_usd is not None:
            return session.reported_cost_usd
        return self.estimate_usd(session.model, session.usage)


def _parse_model_price(name: str, raw: object) -> ModelPrice | None:
    if not isinstance(raw, dict):
        logger.warning("Ignoring pricing for %s: expected a table", name)
        return None
    try:
        return ModelPrice(
            input=float(raw["input"]),
            output=float(raw["output"]),
            cache_read=float(raw.get("cache_read", 0.0)),
            cache_write=float(raw.get("cache_write", 0.0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("Ignoring pricing for %s: %s", name, exc)
        return None


def load_price_book(path: Path = DEFAULT_PRICING_PATH) -> PriceBook:
    if not path.is_file():
        return PriceBook()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Could not read pricing file %s: %s", path, exc)
        return PriceBook()
    prices: dict[str, ModelPrice] = {}
    for name, raw in data.get("models", {}).items():
        parsed = _parse_model_price(name, raw)
        if parsed is not None:
            prices[name] = parsed
    return PriceBook(prices)
