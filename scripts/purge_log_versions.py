"""LOG-4 · S-8 · the scheduled 90-day version purge (decision S-20).

    uv run python scripts/purge_log_versions.py            # every tenant
    uv run python scripts/purge_log_versions.py --dry-run  # count, delete nothing

Runs as the **system identity**, not as an Admin: the audit rows say ``system``
rather than naming whichever account was borrowed. See
``relay.app.logs.retention`` for why that mattered enough to build an identity
for.

Cron, on one host only — two concurrent runs are safe (the delete is
idempotent) but they would double the load for nothing::

    17 4 * * *  cd /srv/relay && uv run python \
        scripts/purge_log_versions.py >> /var/log/relay/purge.log 2>&1

**Why this is not wired to a request.** Autosave means versions accumulate every
few seconds per active editor, so the purge deletes a lot of rows; triggered from
an application request it would eventually run inside somebody's page load. And
``system_principal`` refuses any origin but ``SYSTEM``, so the HTTP layer cannot
reach it even by accident.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from relay.app.logs.retention import RETENTION, purge_every_tenant


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete log versions older than 90 days (S-8).")
    parser.add_argument(
        "--retention-days",
        type=int,
        default=RETENTION.days,
        help="override the 90-day window; the decided value is 90 (S-8)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="count what the same selection would delete, delete nothing",
    )
    args = parser.parse_args()

    counted = purge_every_tenant(
        retention=dt.timedelta(days=args.retention_days), dry_run=args.dry_run
    )
    verb = "would be deleted" if args.dry_run else "deleted"
    for slug, count in sorted(counted.items()):
        print(f"{slug}: {count} versions {verb}")
    print(f"total: {sum(counted.values())} ({args.retention_days}-day window)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
