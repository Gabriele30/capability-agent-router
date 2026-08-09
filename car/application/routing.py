"""Compose configuration, providers, and the provider-neutral routing core."""

from collections.abc import Callable

from car.config.models import CarConfig
from car.providers.base import ClassificationProvider
from car.providers.gemini import GeminiProvider, GeminiProviderConfig
from car.repository.models import RepositoryState
from car.router.consultation import RoutingEvaluation, evaluate_routing
from car.router.models import TaskRequest, UserMode

ProviderFactory = Callable[[GeminiProviderConfig], ClassificationProvider]


def evaluate_analysis(
    task: TaskRequest,
    repository: RepositoryState,
    requested_mode: UserMode,
    config: CarConfig,
    provider_factory: ProviderFactory = GeminiProvider,
) -> tuple[UserMode, RoutingEvaluation]:
    """Evaluate routing with the configured advisory provider, if eligible.

    The provider's local health check remains the single authority for disabled,
    incomplete, or credential-missing Gemini configuration. No provider-specific
    behavior is added to the deterministic router.
    """
    mode = requested_mode if requested_mode != UserMode.AUTO else config.default_mode
    provider = provider_factory(config.providers.gemini)
    return mode, evaluate_routing(task, repository, mode, provider)
