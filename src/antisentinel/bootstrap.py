"""Composition root for wiring the local AntiSentinel application."""

from antisentinel.api.app import create_app
from antisentinel.entry.application import DiagnosisApplicationService


def build_application():
    """Build a FastAPI application using environment-selected providers."""
    return create_app(DiagnosisApplicationService.from_environment())
