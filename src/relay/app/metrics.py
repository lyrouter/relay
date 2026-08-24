"""INT-8 · the acceptance dashboard, with its denominators pinned here.

**The point of this module is the denominators, not the numbers.** "Weekly active
creator share" is meaningless until somebody says share *of what*, over *which*
week — and if that is settled during the acceptance review it will be settled by
whoever is most confident in the room, after the numbers are already on screen.
So the definitions are code:

* **the week** is the current **calendar** week in the tenant's timezone, Monday
  00:00 to now. Not "the last seven days": a rolling window makes Monday's number
  incomparable to Friday's, and the metric exists to be compared;
* **the denominator** is **activated accounts** — ``status = active``. Not every
  row in ``user``: pending signups nobody approved and deactivated leavers would
  both dilute the share and make it drift downwards as the team *succeeds* at
  offboarding;
* **the numerator** is activated accounts that authored a log or filed a ticket in
  that week. Authoring, not reading: the metric is about the workbench being used
  to write things down, which is the thing S1 is trying to establish.

⚠️ **Service principals are excluded from every people-metric**, which is why this
counts by ``author_id`` / ``reporter_id`` joined to active *users*: a ticket filed
by a service token has ``reporter_id = NULL`` (S-10), so it cannot inflate a
headcount even by accident. One alerting script would otherwise look like the
team's most productive member — the failure INT-8 names explicitly.

The knowledge count is **not** redefined here; it is ``LogService``'s (S-16:
checked **and** body ≥ 300 characters), reused, so the dashboard and the product
cannot disagree about what a knowledge candidate is. Ten of them still get spot-
checked by hand before the review (P-4) — a count is not a quality judgement.
"""

from __future__ import annotations

import datetime as dt
import uuid
import zoneinfo
from dataclasses import dataclass

from sqlalchemy import distinct, func, select

from relay.app.authz import actor_principal, require
from relay.app.logs.service import KNOWLEDGE_MIN_BODY
from relay.domain.enums import UserStatus, WebhookDeliveryState
from relay.domain.permissions import Capability
from relay.infra.db.models import Log, Tenant, Ticket, User, WebhookDelivery
from relay.infra.db.session import tenant_session

#: Monday. Written as a constant because "the week starts on Monday" is a
#: reporting decision, not a fact about calendars.
WEEK_STARTS_ON = 0


@dataclass(frozen=True, slots=True)
class WeeklyCreators:
    """The headline metric, carrying its own denominator so a screenshot of it
    cannot be quoted without one."""

    week_start: dt.datetime
    #: Activated accounts — the denominator. See the module note.
    activated_accounts: int
    #: Activated accounts that authored a log or filed a ticket this week.
    active_creators: int

    @property
    def share(self) -> float:
        """0.0 when there is nobody to count, rather than a division error.

        A tenant with no activated accounts has a share of zero by any reading,
        and a dashboard that raises on an empty tenant is a dashboard nobody
        opens on day one.
        """
        if not self.activated_accounts:
            return 0.0
        return self.active_creators / self.activated_accounts


@dataclass(frozen=True, slots=True)
class Dashboard:
    tenant_slug: str
    generated_at: dt.datetime
    creators: WeeklyCreators
    logs_this_week: int
    tickets_this_week: int
    #: LOG-9 / S-16, computed by the product's own rule.
    knowledge_candidates: int
    tickets_by_status: dict[str, int]
    #: API-4's observability (§8.5). Zeroes when no webhook exists, which is the
    #: normal state before the first integration.
    webhook_delivered: int
    webhook_pending: int
    webhook_dead_letter: int


class AcceptanceDashboard:
    """Runs inside a ``TenantContext``. One query set, one snapshot."""

    def snapshot(self, *, now: dt.datetime | None = None) -> Dashboard:
        now = now or dt.datetime.now(dt.UTC)
        with tenant_session() as session:
            actor = actor_principal(session)
            # A read of aggregate activity, so ``CONTENT_VIEW`` — the acceptance
            # review is a team conversation and the numbers are not a secret from
            # the team. Nothing here exposes an individual's content: a Guest sees
            # counts, never titles.
            require(actor, Capability.CONTENT_VIEW)

            tenant = session.get(Tenant, actor.tenant_id)
            week_start = _week_start(now, getattr(tenant, "timezone", None))

            activated = int(
                session.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(User.status == UserStatus.ACTIVE)
                )
                or 0
            )

            authors = set(
                session.scalars(
                    select(distinct(Log.author_id))
                    .join(User, User.id == Log.author_id)
                    .where(Log.created_at >= week_start, User.status == UserStatus.ACTIVE)
                )
            )
            reporters = set(
                session.scalars(
                    select(distinct(Ticket.reporter_id))
                    .join(User, User.id == Ticket.reporter_id)
                    .where(
                        Ticket.created_at >= week_start,
                        User.status == UserStatus.ACTIVE,
                    )
                )
            )
            # The join to ``user`` is what excludes machine principals: a
            # service-token ticket has no reporter row to join to.
            creators = {one for one in authors | reporters if one is not None}

            logs_this_week = int(
                session.scalar(
                    select(func.count()).select_from(Log).where(Log.created_at >= week_start)
                )
                or 0
            )
            tickets_this_week = int(
                session.scalar(
                    select(func.count())
                    .select_from(Ticket)
                    .where(Ticket.created_at >= week_start)
                )
                or 0
            )
            knowledge = int(
                session.scalar(
                    select(func.count())
                    .select_from(Log)
                    .where(
                        Log.knowledge_candidate.is_(True),
                        func.char_length(Log.body) >= KNOWLEDGE_MIN_BODY,
                    )
                )
                or 0
            )
            by_status = {
                str(status): int(count)
                for status, count in session.execute(
                    select(Ticket.status, func.count()).group_by(Ticket.status)
                )
            }
            deliveries = {
                str(state): int(count)
                for state, count in session.execute(
                    select(WebhookDelivery.state, func.count()).group_by(WebhookDelivery.state)
                )
            }

            return Dashboard(
                tenant_slug=getattr(tenant, "slug", ""),
                generated_at=now,
                creators=WeeklyCreators(
                    week_start=week_start,
                    activated_accounts=activated,
                    active_creators=len(creators),
                ),
                logs_this_week=logs_this_week,
                tickets_this_week=tickets_this_week,
                knowledge_candidates=knowledge,
                tickets_by_status=by_status,
                webhook_delivered=deliveries.get(str(WebhookDeliveryState.DELIVERED), 0),
                webhook_pending=deliveries.get(str(WebhookDeliveryState.PENDING), 0),
                webhook_dead_letter=deliveries.get(str(WebhookDeliveryState.DEAD_LETTER), 0),
            )


def _week_start(now: dt.datetime, timezone: str | None) -> dt.datetime:
    """Monday 00:00 in the tenant's timezone, as an aware UTC-comparable instant.

    In the **tenant's** zone rather than UTC because the week people mean is the
    week they worked: with Asia/Shanghai, a UTC week boundary would put Monday
    morning's writing into the previous week's number.
    """
    zone = dt.UTC
    if timezone:
        try:
            zone = zoneinfo.ZoneInfo(timezone)
        except zoneinfo.ZoneInfoNotFoundError:  # pragma: no cover - bad config
            zone = dt.UTC
    local = now.astimezone(zone)
    monday = local - dt.timedelta(days=(local.weekday() - WEEK_STARTS_ON) % 7)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def creator_ids_this_week(*, now: dt.datetime | None = None) -> set[uuid.UUID]:
    """Who counted, for a review that asks "which people?" rather than "how many".

    Separate from :meth:`AcceptanceDashboard.snapshot` because the dashboard
    should not carry a list of names around; this is for someone digging into a
    number they doubt.
    """
    snapshot_now = now or dt.datetime.now(dt.UTC)
    with tenant_session() as session:
        actor = actor_principal(session)
        require(actor, Capability.USER_MANAGE)
        tenant = session.get(Tenant, actor.tenant_id)
        week_start = _week_start(snapshot_now, getattr(tenant, "timezone", None))
        authors = set(
            session.scalars(
                select(distinct(Log.author_id))
                .join(User, User.id == Log.author_id)
                .where(Log.created_at >= week_start, User.status == UserStatus.ACTIVE)
            )
        )
        reporters = set(
            session.scalars(
                select(distinct(Ticket.reporter_id))
                .join(User, User.id == Ticket.reporter_id)
                .where(Ticket.created_at >= week_start, User.status == UserStatus.ACTIVE)
            )
        )
        return {one for one in authors | reporters if one is not None}
