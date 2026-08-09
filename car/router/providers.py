"""Compatibility re-exports; provider contracts now live in :mod:`car.providers`."""

from car.providers.base import ClassificationProvider as AgentProvider
from car.providers.models import ProviderCapabilities, ProviderHealth

__all__ = ["AgentProvider", "ProviderCapabilities", "ProviderHealth"]
