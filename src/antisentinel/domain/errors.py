"""Domain-level errors."""


class DomainError(Exception):
    """Base exception for domain validation and lifecycle failures."""


class InvalidInputError(DomainError):
    """Raised when a domain object receives invalid input."""


class InvalidTransitionError(DomainError):
    """Raised when an entity cannot perform an action in its current state."""

