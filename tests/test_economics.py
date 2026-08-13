import pytest

from car.economics.pricing import DEFAULT_PRICE_CATALOG, ReferenceCostCalculator
from car.telemetry.models import TokenUsage, UsageSource


def test_flash_lite_reference_cost_counts_thinking_once():
    cost = ReferenceCostCalculator().calculate(
        provider="gemini",
        model="gemini-3.5-flash-lite",
        usage=TokenUsage(
            input_tokens=1000,
            output_tokens=100,
            reasoning_tokens=50,
            source=UsageSource.PROVIDER_REPORTED,
        ),
    )
    assert cost.complete
    assert cost.reference_inference_cost_usd == (1000 * 0.3 + 150 * 2.5) / 1_000_000


def test_flash_lite_cached_input_is_unpriced_and_fails_closed():
    cost = ReferenceCostCalculator().calculate(
        provider="gemini",
        model="gemini-3.5-flash-lite",
        usage=TokenUsage(
            input_tokens=1000,
            cached_input_tokens=200,
            output_tokens=100,
            source=UsageSource.PROVIDER_REPORTED,
        ),
    )

    assert not cost.complete
    assert cost.reference_inference_cost_usd is None
    assert cost.missing_dimensions == ("cached_input_tokens",)


def test_unknown_model_and_incomplete_attempt_make_cost_unavailable():
    calculator = ReferenceCostCalculator()
    unknown = calculator.calculate(
        provider="gemini",
        model="unknown",
        usage=TokenUsage(input_tokens=1, output_tokens=1, source=UsageSource.PROVIDER_REPORTED),
    )
    assert not unknown.complete and unknown.reference_inference_cost_usd is None
    assert not calculator.aggregate((unknown,)).complete


def test_exact_catalog_models_exist():
    flash_lite = DEFAULT_PRICE_CATALOG.lookup("gemini", "gemini-3.5-flash-lite")
    assert flash_lite is not None
    assert flash_lite.input_per_million_usd == 0.3
    assert flash_lite.output_per_million_usd == 2.5
    assert flash_lite.cached_input_per_million_usd is None
    assert DEFAULT_PRICE_CATALOG.lookup("gemini", "gemini-3.6-flash") is None
    assert DEFAULT_PRICE_CATALOG.lookup("codex", "gpt-5.6-sol")
    assert DEFAULT_PRICE_CATALOG.lookup("codex", "gpt-5.6-terra")


def test_unavailable_codex_usage_is_never_priced_as_zero_and_failures_aggregate():
    calculator = ReferenceCostCalculator()
    codex = calculator.calculate(provider="codex", model="gpt-5.6-sol", usage=None)
    gemini = calculator.calculate(
        provider="gemini",
        model="gemini-3.5-flash-lite",
        usage=TokenUsage(input_tokens=1, output_tokens=1, source=UsageSource.PROVIDER_REPORTED),
    )
    aggregate = calculator.aggregate((gemini, codex))
    assert not codex.complete and codex.reference_inference_cost_usd is None
    assert not aggregate.complete and aggregate.reference_inference_cost_usd is None


def test_codex_cache_write_and_reasoning_breakdown_are_billed_once():
    cost = ReferenceCostCalculator().calculate(
        provider="codex",
        model="gpt-5.6-sol",
        usage=TokenUsage(
            input_tokens=1000,
            cached_input_tokens=200,
            cache_write_input_tokens=100,
            output_tokens=50,
            reasoning_tokens=40,
            reasoning_tokens_included_in_output=True,
            source=UsageSource.PROVIDER_REPORTED,
        ),
    )
    assert cost.complete
    assert cost.reference_inference_cost_usd == pytest.approx(
        (700 * 5 + 200 * 0.5 + 100 * 6.25 + 50 * 30) / 1_000_000
    )


def test_terra_reference_cost_uses_published_rates_and_reasoning_once():
    cost = ReferenceCostCalculator().calculate(
        provider="codex",
        model="gpt-5.6-terra",
        usage=TokenUsage(
            input_tokens=1000,
            cached_input_tokens=200,
            cache_write_input_tokens=100,
            output_tokens=50,
            reasoning_tokens=40,
            reasoning_tokens_included_in_output=True,
            source=UsageSource.PROVIDER_REPORTED,
        ),
    )
    assert cost.complete
    assert cost.reference_inference_cost_usd == pytest.approx(
        (700 * 2.5 + 200 * 0.25 + 100 * 3.125 + 50 * 15) / 1_000_000
    )


def test_inconsistent_cache_breakdown_fails_closed():
    cost = ReferenceCostCalculator().calculate(
        provider="codex",
        model="gpt-5.6-sol",
        usage=TokenUsage(
            input_tokens=10,
            cached_input_tokens=8,
            cache_write_input_tokens=3,
            output_tokens=1,
            source=UsageSource.PROVIDER_REPORTED,
        ),
    )
    assert not cost.complete and cost.reference_inference_cost_usd is None
