"""The public ticket API (``/api/v1``) — API-1/2/3/4/5/6, design §8.

**This is a contract layer, not a second implementation** (§8.1). Every route here
calls the same application-layer use case the Web UI calls. What it adds on top is
four things and nothing else: token authentication, the wire shape, idempotency,
and the error format. The moment it grows its own state-machine check or its own
permission rule, the two surfaces start to drift — and drift shows up as "changing
it in the UI notifies people, changing it over the API doesn't", which nobody
spots by reading code. ``import-linter`` keeps the routers out of the repository
layer so that this cannot happen quietly.

**Different discipline from ``/web``**, deliberately (§8.9):

============  ====================================  ============================
              ``/web/*``                             ``/api/v1/*``
============  ====================================  ============================
consumer      the Vue frontend in this repository    external systems
versioning    none — ships with its consumer         v1, **frozen on release**
auth          session cookie (HttpOnly, SameSite)    ``Authorization: Bearer``
snapshot      not in the OpenAPI snapshot            in it; a field change needs
                                                     a human to update the snapshot
============  ====================================  ============================

**Frozen means frozen.** Inside v1 only additive change is allowed: new fields,
new optional parameters, new enum values (consumers must tolerate unknown ones —
that is in the integration guide). Deleting or retyping a field, or changing what
a status value means, is a v2 with 90 days of overlap. ``tests/test_frozen_contract.py``
and the ``openapi.json`` snapshot gate are what make that mechanical instead of
remembered.

**Reserved, not implemented**: ``/logs`` and ``/search`` (§8.3). The namespaces are
claimed now so that when they land they inherit this surface's pagination and
error conventions rather than growing a second set — see :mod:`.reserved`.
"""

from relay.api.v1 import meta, reserved, tickets, webhooks

#: Every router this surface mounts, in the order ``relay.api.app`` includes them.
#: Ordering matters for one pair only: ``reserved`` claims prefixes that must not
#: shadow a real route, so it goes last.
ROUTERS = (tickets.router, meta.router, webhooks.router, reserved.router)

__all__ = ["ROUTERS", "meta", "reserved", "tickets", "webhooks"]
