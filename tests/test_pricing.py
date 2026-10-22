from pathlib import Path

import pytest

from agentdeck.models import AgentSession, TokenUsage
from agentdeck.pricing import ModelPrice, PriceBook, load_price_book


def _session(model: str, reported: float | None = None) -> AgentSession:
    return AgentSession(
        agent="Claude Code",
        session_id="s1",
        title="",
        model=model,
        cwd="",
        started_at=None,
        last_active_at=None,
        usage=TokenUsage(input_tokens=1_000_000, output_tokens=500_000, cache_read_tokens=2_000_000),
        reported_cost_usd=reported,
    )


def test_estimate_uses_per_million_rates_for_every_token_kind() -> None:
    book = PriceBook({"model-a": ModelPrice(input=2.0, output=10.0, cache_read=0.5)})
    assert book.estimate_usd("model-a", _session("model-a").usage) == pytest.approx(2.0 + 5.0 + 1.0)


def test_lookup_prefers_longest_matching_prefix() -> None:
    book = PriceBook({"claude": ModelPrice(1, 1), "claude-opus-5": ModelPrice(9, 9)})
    assert book.lookup("claude-opus-5-20261001") == ModelPrice(9, 9)
    assert book.lookup("claude-haiku") == ModelPrice(1, 1)


def test_lookup_is_case_insensitive_and_unknown_model_has_no_price() -> None:
    book = PriceBook({"GPT-6": ModelPrice(1, 2)})
    assert book.lookup("gpt-6-sol") == ModelPrice(1, 2)
    assert book.estimate_usd("glm-5", TokenUsage(input_tokens=10)) is None


def test_agent_reported_cost_wins_over_estimate() -> None:
    book = PriceBook({"m": ModelPrice(100, 100)})
    assert book.session_cost_usd(_session("m", reported=0.25)) == 0.25


def test_missing_pricing_file_yields_empty_book(tmp_path: Path) -> None:
    assert len(load_price_book(tmp_path / "absent.toml")) == 0


def test_pricing_file_is_loaded_and_bad_entries_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "pricing.toml"
    path.write_text(
        '[models."good"]\ninput = 1.5\noutput = 6\n'
        '[models."no-output"]\ninput = 1\n'
        '[models."bad-number"]\ninput = "x"\noutput = 1\n',
        encoding="utf-8",
    )
    book = load_price_book(path)
    assert len(book) == 1
    assert book.lookup("good") == ModelPrice(input=1.5, output=6.0)


def test_corrupt_pricing_file_yields_empty_book(tmp_path: Path) -> None:
    path = tmp_path / "pricing.toml"
    path.write_text("[models\nbroken", encoding="utf-8")
    assert len(load_price_book(path)) == 0


def test_example_pricing_file_parses() -> None:
    example = Path(__file__).resolve().parents[1] / "pricing.example.toml"
    assert len(load_price_book(example)) >= 1
