"""Gemini adapter boundary. Live classification is intentionally deferred to 4B."""

import importlib.util
import os

from pydantic import BaseModel, Field

from car.providers.models import ProviderCapabilities, ProviderHealth, ProviderStatus


class GeminiProviderConfig(BaseModel):
    enabled: bool = False
    model: str | None = None
    api_key_env: str = "GEMINI_API_KEY"
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_attempts: int = Field(default=2, ge=1, le=3)


class GeminiProvider:
    """Local-only Gemini configuration inspection for the 4A provider foundation."""

    def __init__(
        self, config: GeminiProviderConfig, environment: dict[str, str] | None = None
    ) -> None:
        self.config = config
        self._environment = environment if environment is not None else os.environ

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_classification=True)

    def health(self) -> ProviderHealth:
        if not self.config.enabled:
            return ProviderHealth(status=ProviderStatus.DISABLED, detail="provider disabled")
        if not self.config.model:
            return ProviderHealth(
                status=ProviderStatus.NOT_CONFIGURED, detail="model not configured"
            )
        if not self._environment.get(self.config.api_key_env):
            return ProviderHealth(
                status=ProviderStatus.MISSING_CREDENTIALS, detail="credential unavailable"
            )
        if importlib.util.find_spec("google.genai") is None:
            return ProviderHealth(status=ProviderStatus.NOT_CONFIGURED, detail="SDK unavailable")
        return ProviderHealth(
            status=ProviderStatus.CONFIGURED, configured=True, model=self.config.model
        )
