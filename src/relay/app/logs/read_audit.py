"""S-19 · the second half of "an Admin reads every share level".

The decision confirmed that an Admin may read a colleague's private log (§6.3).
The other half of the same decision is that **the read leaves a trail**: a
whole-tenant read permission with no record cannot be reviewed after the fact,
and after the fact is the only time anyone needs to.

Three rules shape what lands in ``audit_log``, and each exists to keep the trail
worth reading:

* **Only reads the role made possible.** The counterfactual lives in
  :func:`relay.app.logs.sharing.reached_only_by_role` — would an ordinary Member
  have seen this? An Admin opening an L3 log, or an L1 log they were named on,
  writes nothing.
* **Never ordinary browsing.** A Member reading their own space's logs writes
  nothing at all, from any path. Recording every read would bury the twenty rows
  that matter under a hundred thousand that do not, which is the same as not
  recording them.
* **One row per call, not per row read.** A list or a search that surfaces
  fourteen private logs writes a single row naming them
  (``after["log_ids"]``), because what happened *was* one act.

The write joins the caller's transaction like every other audit row
(:mod:`relay.app.audit`), which means a read path that records something has to
commit — see :meth:`relay.app.logs.service.LogService.get`. That is the honest
cost of auditing reads and it is why the check is narrow: the common path stays
a pure read with nothing to flush.

``via`` says which surface the read came through (``get`` / ``versions`` /
``diff`` / ``list`` / ``search`` / ``attachment``). It is in the payload rather
than in the action name so that "show me everything an Admin read that was not
theirs" stays one query.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select

from relay.app import audit
from relay.app.authz import Principal
from relay.app.logs.sharing import Reader, reached_only_by_role
from relay.domain.enums import Role, ShareLevel
from relay.infra.db.models import Log, LogShareGrant, SpaceMember

#: One action for every privileged read, whatever the surface.
ACTION = "log.read_by_admin"

#: A list or search that surfaced more than this many privileged logs records the
#: count and the first ``MAX_IDS`` ids. A JSONB array is not the place for an
#: unbounded list, and the number is the part that says "go look at this".
MAX_IDS = 50


def is_privileged(
    actor: Principal,
    *,
    share_level: ShareLevel,
    author_id: uuid.UUID,
    has_named_grant: bool = False,
    is_space_member: bool = False,
) -> bool:
    """Would this read have failed for an ordinary Member?

    Exposed so a caller can decide whether the facts are even worth looking up:
    the two membership queries below are wasted on the overwhelmingly common
    case of somebody reading their own log.
    """
    if actor.role is not Role.ADMIN or actor.user_id is None:
        return False
    return reached_only_by_role(
        share_level=share_level,
        author_id=author_id,
        reader=Reader(user_id=actor.user_id, role=actor.role),
        has_named_grant=has_named_grant,
        is_space_member=is_space_member,
    )


def record_one(
    session,
    actor: Principal,
    log,
    *,
    via: str,
    has_named_grant: bool = False,
    is_space_member: bool = False,
) -> bool:
    """Audit a single privileged read. Returns whether a row was added.

    The return value is the caller's signal to commit — a read path that always
    committed would turn every page view into a write transaction.
    """
    if not is_privileged(
        actor,
        share_level=log.share_level,
        author_id=log.author_id,
        has_named_grant=has_named_grant,
        is_space_member=is_space_member,
    ):
        return False
    audit.record(
        session,
        ACTION,
        target_type="log",
        target_id=log.id,
        after={
            "via": via,
            "share_level": str(log.share_level),
            "author_id": str(log.author_id),
        },
    )
    return True


def record_many(session, actor: Principal, logs: Iterable, *, via: str) -> int:
    """Audit one multi-log read. Returns how many logs were privileged.

    Takes ORM logs already loaded by the caller, and looks up grants and space
    membership **once** for the whole batch rather than per row: a list of fifty
    would otherwise be a hundred queries to produce one audit row.
    """
    if actor.role is not Role.ADMIN or actor.user_id is None:
        return 0

    candidates = [
        log
        for log in logs
        if log.author_id != actor.user_id and log.share_level is not ShareLevel.TENANT
    ]
    if not candidates:
        return 0

    granted, spaces = _reach_of(session, actor.user_id)
    privileged = [
        log
        for log in candidates
        if is_privileged(
            actor,
            share_level=log.share_level,
            author_id=log.author_id,
            has_named_grant=log.id in granted,
            is_space_member=log.space_id is not None and log.space_id in spaces,
        )
    ]
    if not privileged:
        return 0

    ids = [str(log.id) for log in privileged]
    audit.record(
        session,
        ACTION,
        target_type="log",
        # No single target: the act was one call over many logs. The ids are in
        # the payload, and the count is there even when the list is truncated.
        target_id=None,
        after={"via": via, "count": len(ids), "log_ids": ids[:MAX_IDS]},
    )
    return len(ids)


def record_by_id(session, actor: Principal, log_ids: Iterable[uuid.UUID], *, via: str) -> int:
    """``record_many`` for a caller holding ids rather than rows — search.

    The hits come back from ``SearchPort`` as ids, and the share level is not
    part of a hit. Loading the rows here keeps the rule in one place instead of
    teaching the search adapter about privilege.
    """
    wanted = list(dict.fromkeys(log_ids))
    if not wanted:
        return 0
    logs = session.scalars(select(Log).where(Log.id.in_(wanted))).all()
    return record_many(session, actor, logs, via=via)


def _reach_of(session, user_id: uuid.UUID) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """The two facts the rule needs, for this reader, in two queries."""
    granted = set(
        session.scalars(
            select(LogShareGrant.log_id).where(LogShareGrant.user_id == user_id)
        ).all()
    )
    spaces = set(
        session.scalars(
            select(SpaceMember.space_id).where(SpaceMember.user_id == user_id)
        ).all()
    )
    return granted, spaces
