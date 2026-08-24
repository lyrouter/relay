"""The contract layer: HTTP in, use cases out.

Nothing here decides anything a use case could decide. A router parses the
request, authorizes the *transport* (session, CSRF, and for ``/api/v1`` the
token), calls one application-layer use case, and serializes the result — which
is §8.1's rule that the API is a contract layer and not a second implementation,
and it is enforced by ``import-linter`` rather than by review.

Start with :mod:`relay.api.dependencies` (how a request becomes a
``TenantContext``) and :mod:`relay.api.problems` (how a failure becomes RFC 9457).
"""

from relay.api.app import create_app

__all__ = ["create_app"]
