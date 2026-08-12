from datetime import date

from car.economics.models import AttemptCost, ExecutionCost, ModelPrice, ReferencePriceCatalog
from car.telemetry.models import TokenUsage, UsageSource

DEFAULT_PRICE_CATALOG = ReferencePriceCatalog(
    version="2026-08-11",
    prices=(
        ModelPrice(
            provider="gemini",
            model="gemini-3.6-flash",
            input_per_million_usd=1.5,
            cached_input_per_million_usd=0.15,
            output_per_million_usd=7.5,
            source_url="https://ai.google.dev/gemini-api/docs/pricing",
            source_label="Google Gemini Developer API Paid Tier Standard",
            verified_on=date(2026, 8, 11),
            source_last_updated=date(2026, 7, 21),
        ),
        ModelPrice(
            provider="codex",
            model="gpt-5.6-sol",
            input_per_million_usd=5,
            cached_input_per_million_usd=0.5,
            cache_write_input_per_million_usd=6.25,
            output_per_million_usd=30,
            source_url="https://developers.openai.com/api/docs/models/gpt-5.6-sol",
            source_label="OpenAI public API list price",
            verified_on=date(2026, 8, 11),
            long_context_threshold=272000,
            long_context_input_multiplier=2,
            long_context_output_multiplier=1.5,
        ),
    ),
)


class ReferenceCostCalculator:
    def __init__(self, catalog: ReferencePriceCatalog = DEFAULT_PRICE_CATALOG) -> None:
        self._catalog = catalog

    def calculate(
        self, *, provider: str | None, model: str | None, usage: TokenUsage | None
    ) -> AttemptCost:
        price = self._catalog.lookup(provider or "", model or "")
        if price is None or usage is None or usage.source == UsageSource.UNAVAILABLE:
            return AttemptCost(complete=False, missing_dimensions=("model_or_usage",))
        if usage.input_tokens is None or usage.output_tokens is None:
            return AttemptCost(complete=False, missing_dimensions=("input_tokens", "output_tokens"))
        cached = usage.cached_input_tokens or 0
        cache_write = usage.cache_write_input_tokens or 0
        if cached + cache_write > usage.input_tokens:
            return AttemptCost(complete=False, missing_dimensions=("consistent_input_breakdown",))
        if cache_write and price.cache_write_input_per_million_usd is None:
            return AttemptCost(complete=False, missing_dimensions=("cache_write_input_tokens",))
        input_rate, output_rate = price.input_per_million_usd, price.output_per_million_usd
        if price.long_context_threshold and usage.input_tokens > price.long_context_threshold:
            input_rate *= price.long_context_input_multiplier or 1
            output_rate *= price.long_context_output_multiplier or 1
        input_cost = (usage.input_tokens - cached - cache_write) * input_rate / 1_000_000
        cached_cost = cached * (price.cached_input_per_million_usd or input_rate) / 1_000_000
        cache_write_cost = cache_write * (price.cache_write_input_per_million_usd or 0) / 1_000_000
        output_cost = (
            (
                usage.output_tokens
                + (0 if usage.reasoning_tokens_included_in_output else usage.reasoning_tokens or 0)
            )
            * output_rate
            / 1_000_000
        )
        return AttemptCost(
            complete=True,
            reference_input_cost_usd=input_cost,
            reference_cached_input_cost_usd=cached_cost,
            reference_cache_write_input_cost_usd=cache_write_cost,
            reference_output_cost_usd=output_cost,
            reference_inference_cost_usd=input_cost + cached_cost + cache_write_cost + output_cost,
        )

    def aggregate(self, attempts: tuple[AttemptCost, ...]) -> ExecutionCost:
        if not all(attempt.complete for attempt in attempts):
            return ExecutionCost(attempts=attempts, complete=False)
        return ExecutionCost(
            attempts=attempts,
            complete=True,
            reference_inference_cost_usd=sum(
                attempt.reference_inference_cost_usd or 0 for attempt in attempts
            ),
        )
