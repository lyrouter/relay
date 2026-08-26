"""AC-9 · admit more email domains to an existing tenant.

    uv run python scripts/add_allowed_domains.py \
        --tenant-slug gateway \
        --allowed-domain lyrouter.com \
        --allowed-domain arraynetworks.com.cn

Bootstrap only writes the allowlist at creation, and re-running it is a no-op.
This is the follow-up: same one-to-one domain ↔ tenant rule, same default
role / auto-join as the tenant already uses, unless overridden.

Idempotent: a domain the tenant already has is reported, not an error.
"""

from __future__ import annotations

import argparse
import sys

from relay.app.accounts.bootstrap import BootstrapError, add_allowed_domains
from relay.domain.enums import Role


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add self-service signup domains to an existing tenant (AC-9)."
    )
    parser.add_argument("--tenant-slug", required=True)
    parser.add_argument(
        "--allowed-domain",
        action="append",
        required=True,
        dest="allowed_domains",
        help="repeatable; a bare domain or an address whose domain should be admitted",
    )
    parser.add_argument(
        "--no-auto-join",
        action="store_true",
        help="new domains wait for Admin approval; default is to copy the tenant's existing flag",
    )
    parser.add_argument(
        "--auto-join",
        action="store_true",
        help="new domains join directly; default is to copy the tenant's existing flag",
    )
    parser.add_argument("--default-role", choices=[r.value for r in Role], default=None)
    args = parser.parse_args()
    if args.auto_join and args.no_auto_join:
        print("pass at most one of --auto-join / --no-auto-join", file=sys.stderr)
        return 2

    auto_join: bool | None = None
    if args.auto_join:
        auto_join = True
    elif args.no_auto_join:
        auto_join = False

    try:
        result = add_allowed_domains(
            args.tenant_slug,
            tuple(args.allowed_domains),
            auto_join=auto_join,
            default_role=Role(args.default_role) if args.default_role else None,
        )
    except BootstrapError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1

    print(f"tenant {args.tenant_slug!r}")
    print(f"  tenant_id : {result.tenant_id}")
    if result.added:
        print(f"  added     : {', '.join(result.added)}")
    if result.already_present:
        print(f"  already   : {', '.join(result.already_present)}")
    if result.added:
        print("\nSelf-service signup is now open to those domains.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
