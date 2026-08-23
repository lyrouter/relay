"""RepositoryPort — tenant filtering, and where it actually lives.

Read this before writing a repository: **the tenant filter is not implemented
here.** It is implemented in PostgreSQL by RLS (MT-3). SQLAlchemy only injects
convenience.

That is a deliberate inversion of the usual pattern, and the reason is in design
§4.2: every ORM leaves a raw-SQL exit, so betting architectural safety on ORM
discipline is the one bet to avoid. A useful side effect is that **ORM choice
stops being an architectural decision** — misuse SQLAlchemy and you still cannot
leak a tenant.

What a repository owes, then, is narrower than usual: open its work inside a
``TenantContext`` so the session sets ``app.tenant_id``, and never reach for the
owner or system engine to get around a permission error.
"""

from __future__ import annotations

import uuid
from typing import Protocol, TypeVar

T = TypeVar("T")


class RepositoryPort(Protocol[T]):
    def get(self, entity_id: uuid.UUID) -> T | None:
        """Returns None for a row in another tenant — the row is invisible, not
        forbidden. That is also why the API answers 404 rather than 403 (MT-6):
        a 403 would confirm the resource exists."""
        ...

    def add(self, entity: T) -> T:
        ...
