"""API-4 · the webhook delivery worker (§8.5).

    uv run python scripts/deliver_webhooks.py               # one pass, all tenants
    uv run python scripts/deliver_webhooks.py --loop        # keep draining
    uv run python scripts/deliver_webhooks.py --batch 50    # bigger claim per pass

Cron, one line, on one host — though more than one is safe, which is the whole
point of ``FOR UPDATE SKIP LOCKED``::

    * * * * *  cd /srv/relay && uv run python scripts/deliver_webhooks.py \\
        >> /var/log/relay/webhooks.log 2>&1

**Why a worker rather than sending inside the request.** ``emit`` queues the event
in the same transaction as the ticket write (the outbox), so events cannot describe
a change that rolled back. Sending is the part that must not be in the request
path: a consumer that takes ten seconds would make ticket writes take ten seconds,
and a consumer that is down would make them fail.

**Runs as the system identity, per tenant** — the same S-20 shape as the log
version purge, and for the same reason: a scheduler has no session, and borrowing
an Admin's account would file audit rows against somebody who was asleep. The
tenant *list* is the only cross-tenant read and it goes through
``SystemRepository`` with a written reason.

One deliberate limitation, stated rather than discovered: a minute of cron plus
the 1m/5m/30m/2h/6h ladder means a retry can be up to a minute late. Nobody's
webhook contract promises otherwise, and the alternative is a resident process
S1 does not need.
"""

from __future__ import annotations

import argparse
import sys
import time

from relay.app.logs.retention import system_context
from relay.app.webhooks import WebhookDispatcher
from relay.context import tenant_scope
from relay.infra.db.system_repository import SystemRepository

REASON = "scheduled webhook delivery (API-4)"


def drain_once(*, batch: int) -> dict[str, dict[str, int]]:
    """One pass over every tenant. Returns ``{slug: {outcome: count}}``.

    One tenant per transaction, so a tenant whose delivery fails does not take
    the others down with it — and the counts are per slug rather than one number
    nobody can interpret.
    """
    dispatcher = WebhookDispatcher()
    counted: dict[str, dict[str, int]] = {}
    for tenant in SystemRepository().list_tenants(REASON):
        with tenant_scope(system_context(tenant.id)):
            counted[tenant.slug] = dispatcher.dispatch_batch(limit=batch)
    return counted


def main() -> int:
    parser = argparse.ArgumentParser(description="Deliver queued webhooks (API-4).")
    parser.add_argument(
        "--batch", type=int, default=20, help="how many deliveries to claim per tenant per pass"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="keep draining instead of exiting after one pass (for a supervised process)",
    )
    parser.add_argument(
        "--interval", type=float, default=5.0, help="seconds between passes when --loop is set"
    )
    args = parser.parse_args()

    while True:
        counted = drain_once(batch=args.batch)
        total = {"delivered": 0, "retrying": 0, "dead_letter": 0}
        for slug, outcome in sorted(counted.items()):
            if any(outcome.values()):
                print(
                    f"{slug}: {outcome['delivered']} delivered, "
                    f"{outcome['retrying']} retrying, {outcome['dead_letter']} dead-lettered"
                )
            for name, count in outcome.items():
                total[name] += count
        # Printed every pass, even when idle: §8.5 asks for delivery-rate
        # observability, and a log that only speaks up on failure cannot tell
        # "nothing to do" from "the worker stopped running".
        print(
            f"total: {total['delivered']} delivered, {total['retrying']} retrying, "
            f"{total['dead_letter']} dead-lettered"
        )
        if not args.loop:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
