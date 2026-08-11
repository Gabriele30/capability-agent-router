from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class CostBasis(StrEnum):
    PUBLIC_API_LIST_PRICE = "public_api_list_price"


class ModelPrice(BaseModel):
    provider: str
    model: str
    input_per_million_usd: float = Field(ge=0)
    cached_input_per_million_usd: float | None = Field(default=None, ge=0)
    output_per_million_usd: float = Field(ge=0)
    source_url: str
    source_label: str
    verified_on: date
    source_last_updated: date | None = None
    long_context_threshold: int | None = Field(default=None, ge=1)
    long_context_input_multiplier: float | None = Field(default=None, gt=0)
    long_context_output_multiplier: float | None = Field(default=None, gt=0)


class ReferencePriceCatalog(BaseModel):
    version: str
    prices: tuple[ModelPrice, ...]

    def lookup(self, provider: str, model: str) -> ModelPrice | None:
        return next(
            (price for price in self.prices if price.provider == provider and price.model == model),
            None,
        )


class AttemptCost(BaseModel):
    basis: CostBasis = CostBasis.PUBLIC_API_LIST_PRICE
    complete: bool
    currency: str = "USD"
    reference_input_cost_usd: float | None = None
    reference_cached_input_cost_usd: float | None = None
    reference_output_cost_usd: float | None = None
    reference_inference_cost_usd: float | None = None
    missing_dimensions: tuple[str, ...] = ()


class ExecutionCost(BaseModel):
    attempts: tuple[AttemptCost, ...] = ()
    complete: bool
    reference_inference_cost_usd: float | None = None
