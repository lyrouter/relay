"""TKT-9 · per-tenant ticket numbers (decided, S-12).

``RL-331`` means "the 331st ticket **in this tenant**", not the 331st overall.
That rules out a global sequence, and per-tenant sequences would mean running
DDL whenever a tenant is created.

So: a transaction-scoped advisory lock keyed on the tenant, then ``MAX + 1``.
Two things make it correct rather than merely usual —

* the ``MAX`` runs **under RLS**, so it cannot see another tenant's numbers;
  per-tenant numbering falls out of the policy instead of out of a WHERE clause
  somebody could omit;
* the lock is per tenant, so two tenants creating tickets never wait on each
  other, and ``UNIQUE (tenant_id, number)`` is still there as the backstop if
  this function is ever called outside a transaction that holds the lock.

**Numbers are not guaranteed gap-free.** A transaction that allocates and then
rolls back leaves a hole. That is the right trade: closing it would mean holding
the lock across the whole request, and a permalink whose number skips 47 costs
nobody anything (§7.4 freezes the *format*, not density).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, text

from relay.infra.db.models import Ticket


def next_number(session, tenant_id: uuid.UUID) -> int:
    session.execute(
        # hashtextextended rather than hashtext: hashtext returns int4 and two
        # tenants colliding would serialise them against each other for no
        # reason. A collision is still possible at int8 and still only costs
        # waiting, never correctness — UNIQUE (tenant_id, number) is the
        # correctness guarantee.
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"ticket_number:{tenant_id}"},
    )
    current = session.scalar(select(func.coalesce(func.max(Ticket.number), 0)))
    return int(current or 0) + 1
