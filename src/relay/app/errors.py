"""Application-layer errors, and the one that encodes a security decision.

``NotFound`` for a resource in another tenant is not laziness — it is MT-6 and
design §4.5: **a cross-tenant access returns 404, not 403.** A 403 confirms the
resource exists, which is the fact the tenant boundary exists to hide.

These stay transport-agnostic. §8.6's RFC 9457 ``problem+json`` mapping happens
in the API layer, so the same error can surface as an HTTP body or as a UI
message without the use case knowing which.
"""

from __future__ import annotations


class ApplicationError(Exception):
    """Base. ``message`` is user-facing and must name the next step."""

    #: Machine-discriminable, becomes RFC 9457's `type`.
    code = "error"

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class NotFound(ApplicationError):
    """Also the answer for "exists, but belongs to another tenant"."""

    code = "not_found"


class PermissionDenied(ApplicationError):
    """Only for resources the caller may *know about* but not act on.

    If the caller should not know the resource exists, raise ``NotFound``.
    """

    code = "permission_denied"


class Conflict(ApplicationError):
    code = "conflict"


class ValidationFailed(ApplicationError):
    code = "validation_failed"


class RateLimited(ApplicationError):
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after_seconds: int, detail: dict | None = None):
        super().__init__(message, detail=detail)
        self.retry_after_seconds = retry_after_seconds
