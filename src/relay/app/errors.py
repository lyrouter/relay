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


class TenantInRequest(ApplicationError):
    """§8.2 · a request that tried to name its own tenant.

    Its own code because the design is explicit that this is a **400**, while
    §8.6 keeps 422 for schema validation. The distinction is worth the class: 422
    says "the shape is wrong", and this says "the shape is fine and the request is
    not one we will serve" — a caller who sent a ``tenant_id`` believes it selects
    something, and quietly ignoring it would let them think they wrote to a tenant
    they never touched.
    """

    code = "tenant_in_request"


class ValidationFailed(ApplicationError):
    code = "validation_failed"


class PayloadTooLarge(ApplicationError):
    """A body — in practice an attachment — over the configured limit.

    Its own code rather than ``validation_failed`` because the status differs and
    the client's next move differs: 413 says "send less", 422 says "send it
    differently". Before this existed, ``BlobTooLarge`` escaped the use case as a
    plain ``ValueError`` and every oversize upload answered **500** — a bug that
    read as an outage to the person uploading a screenshot that was too big.
    """

    code = "payload_too_large"


class RateLimited(ApplicationError):
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after_seconds: int, detail: dict | None = None):
        super().__init__(message, detail=detail)
        self.retry_after_seconds = retry_after_seconds
