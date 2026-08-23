"""AC-9 · the credentialed init step for the deployment handbook.

    uv run python scripts/bootstrap_tenant.py \
        --tenant-name "AI 网关团队" --tenant-slug gateway \
        --admin-email li.wang@zerosone.com

The password is read from the RELAY_BOOTSTRAP_PASSWORD environment variable or
prompted for — never a command-line argument, which would land in shell history
and in the process table of every other user on the box.

Idempotent: re-running with the same slug and admin reports the existing tenant
instead of creating a second Admin.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from relay.app.accounts.bootstrap import BootstrapError, BootstrapRequest, bootstrap_tenant
from relay.domain.enums import Role
from relay.domain.passwords import WeakPassword


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first tenant and its Admin (AC-9).")
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--tenant-slug", required=True, help="appears in every permalink (S-12)")
    parser.add_argument("--admin-email", required=True)
    parser.add_argument(
        "--allowed-domain",
        action="append",
        default=[],
        help="repeatable; defaults to the admin's own domain",
    )
    parser.add_argument(
        "--no-auto-join",
        action="store_true",
        help="allowlisted signups wait for Admin approval instead of joining directly",
    )
    parser.add_argument(
        "--default-role", choices=[r.value for r in Role], default=Role.MEMBER.value
    )
    parser.add_argument("--timezone", default="Asia/Shanghai")
    args = parser.parse_args()

    password = os.environ.get("RELAY_BOOTSTRAP_PASSWORD")
    if not password:
        password = getpass.getpass("Admin password: ")
        if password != getpass.getpass("Confirm: "):
            print("passwords do not match", file=sys.stderr)
            return 2

    try:
        result = bootstrap_tenant(
            BootstrapRequest(
                tenant_name=args.tenant_name,
                tenant_slug=args.tenant_slug,
                admin_email=args.admin_email,
                admin_password=password,
                allowed_domains=tuple(args.allowed_domain),
                auto_join=not args.no_auto_join,
                default_role=Role(args.default_role),
                timezone=args.timezone,
            )
        )
    except (BootstrapError, WeakPassword) as exc:
        print(f"bootstrap refused: {exc}", file=sys.stderr)
        return 1

    verb = "created" if result.created else "already present"
    print(f"tenant {args.tenant_slug!r} {verb}")
    print(f"  tenant_id : {result.tenant_id}")
    print(f"  admin     : {args.admin_email} ({result.admin_user_id})")
    print(f"  domains   : {', '.join(result.domains)}")
    if result.created:
        print("\nSelf-service signup is now open to those domains. Nobody else can register.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
