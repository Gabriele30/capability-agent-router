from car.economics.pricing import DEFAULT_PRICE_CATALOG, ReferenceCostCalculator
from car.telemetry.models import TokenUsage, UsageSource


def test_reference_cost_counts_cached_input_and_thinking_once():
    cost = ReferenceCostCalculator().calculate(
        provider="gemini",
        model="gemini-3.6-flash",
        usage=TokenUsage(
            input_tokens=1000,
            cached_input_tokens=200,
            output_tokens=100,
            reasoning_tokens=50,
            source=UsageSource.PROVIDER_REPORTED,
        ),
    )
    assert cost.complete
    assert cost.reference_inference_cost_usd == (800 * 1.5 + 200 * 0.15 + 150 * 7.5) / 1_000_000


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
    assert DEFAULT_PRICE_CATALOG.lookup("gemini", "gemini-3.6-flash")
    assert DEFAULT_PRICE_CATALOG.lookup("codex", "gpt-5.6-sol")


def test_unavailable_codex_usage_is_never_priced_as_zero_and_failures_aggregate():
    calculator = ReferenceCostCalculator()
    codex = calculator.calculate(provider="codex", model="gpt-5.6-sol", usage=None)
    gemini = calculator.calculate(
        provider="gemini",
        model="gemini-3.6-flash",
        usage=TokenUsage(input_tokens=1, output_tokens=1, source=UsageSource.PROVIDER_REPORTED),
    )
    aggregate = calculator.aggregate((gemini, codex))
    assert not codex.complete and codex.reference_inference_cost_usd is None
    assert not aggregate.complete and aggregate.reference_inference_cost_usd is None
