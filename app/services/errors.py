"""Domain errors. The API layer maps these to HTTP responses."""
from __future__ import annotations


class DomainError(Exception):
    status_code: int = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class ValidationError(DomainError):
    status_code = 400


class NotFoundError(DomainError):
    status_code = 404


class ForbiddenError(DomainError):
    status_code = 403


class InsufficientBalanceError(DomainError):
    status_code = 409


class ConflictError(DomainError):
    status_code = 409
