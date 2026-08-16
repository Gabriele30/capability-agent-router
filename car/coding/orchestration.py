"""Validate one coding proposal without applying patches, commands, or retries."""

from car.coding.base import CodingProvider, CodingProviderFailure
from car.coding.models import (
    CodingAttemptResult,
    CodingExecutionPolicy,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
)
from car.providers.models import ProviderError, ProviderErrorKind, ProviderStatus


def attempt_coding(
    context: CodingTaskContext,
    provider: CodingProvider,
    policy: CodingExecutionPolicy | None = None,
) -> CodingAttemptResult:
    """Ask once for a proposal, validate it, and return data without side effects."""
    provider_name = getattr(provider, "name", provider.__class__.__name__)
    provider_model = _provider_model(provider)
    if provider.health().status not in {ProviderStatus.CONFIGURED, ProviderStatus.AVAILABLE}:
        return CodingAttemptResult(
            provider=provider_name,
            model=provider_model,
            attempted=False,
            succeeded=False,
        )
    try:
        proposal = provider.propose(context)
    except CodingProviderFailure as error:
        return _failure(provider_name, provider_model, error.error)
    except RuntimeError as error:
        return _failure(provider_name, provider_model, _normalized_error_kind(str(error)))
    except Exception:
        return _failure(provider_name, provider_model, ProviderErrorKind.UNKNOWN_ERROR)
    if not isinstance(proposal, CodingProposal):
        return _failure(provider_name, provider_model, ProviderErrorKind.INVALID_RESPONSE)
    try:
        _validate_policy(proposal, policy or CodingExecutionPolicy())
    except ValueError:
        return _failure(provider_name, provider_model, ProviderErrorKind.INVALID_REQUEST)
    return CodingAttemptResult(
        provider=provider_name,
        model=provider_model,
        attempted=True,
        succeeded=True,
        proposal=proposal,
        usage=getattr(provider, "last_usage", None),
    )


def _validate_policy(proposal: CodingProposal, policy: CodingExecutionPolicy) -> None:
    if len(proposal.changes) > policy.max_files_per_proposal:
        raise ValueError("proposal exceeds the configured file limit")
    for change in proposal.changes:
        if change.operation == FileChangeOperation.CREATE and not policy.allow_create_files:
            raise ValueError("creating files is disabled by policy")
        if change.operation == FileChangeOperation.MODIFY and not policy.allow_modify_files:
            raise ValueError("modifying files is disabled by policy")


def _failure(
    provider: str, model: str | None, error: ProviderError | ProviderErrorKind
) -> CodingAttemptResult:
    normalized = error if isinstance(error, ProviderError) else ProviderError(kind=error)
    return CodingAttemptResult(
        provider=provider,
        model=model,
        attempted=True,
        succeeded=False,
        error_kind=normalized.kind,
        provider_http_status=normalized.http_status,
        provider_error_status=normalized.status,
        provider_error_message=normalized.message,
    )


def _provider_model(provider: CodingProvider) -> str | None:
    """Read an optional provider-neutral configured model identifier safely."""
    model = getattr(provider, "model", None)
    return model if isinstance(model, str) and model else None


def _normalized_error_kind(value: str) -> ProviderErrorKind:
    try:
        return ProviderErrorKind(value)
    except ValueError:
        return ProviderErrorKind.UNKNOWN_ERROR
