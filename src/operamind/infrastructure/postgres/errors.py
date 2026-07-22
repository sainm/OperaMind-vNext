"""Shared PostgreSQL persistence errors."""


class PersistenceConflictError(RuntimeError):
    """Raised when an immutable ID or idempotency key has different content."""
