"""MT-6 · the negative suite. A CI gate.

Design §4.5: "a deliberately malicious query — including raw SQL — cannot reach
another tenant's row". These tests are written from the attacker's side: each one
tries something a buggy or hostile caller would actually do, and asserts it does
not work.

Read *and* write are both covered on purpose. A read-only check passes happily
while ``INSERT``s stamped with someone else's ``tenant_id`` sail through, because
that is a ``WITH CHECK`` failure, not a ``USING`` one.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text, update

from relay.domain.enums import TicketType
from relay.infra.db.engine import app_engine
from relay.infra.db.models import Label, Log, Space, SpaceMember, Ticket, User
from relay.infra.db.session import tenant_session

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]


def _seed_ticket(
    tenant_id: uuid.UUID, reporter_id: uuid.UUID, number: int, title: str
) -> uuid.UUID:
    ticket = Ticket(
        tenant_id=tenant_id,
        number=number,
        type=TicketType.BUG,
        title=title,
        reporter_id=reporter_id,
    )
    with tenant_session(context_for(tenant_id)) as session:
        session.add(ticket)
        session.commit()
        return ticket.id


# --------------------------------------------------------------------------- read


def test_cross_tenant_read_returns_nothing(tenant_a, tenant_b, user_factory):
    a_user = user_factory(tenant_a, "a@alpha.test")
    b_user = user_factory(tenant_b, "b@bravo.test")
    _seed_ticket(tenant_a, a_user, 1, "alpha ticket")
    b_ticket = _seed_ticket(tenant_b, b_user, 1, "bravo ticket")

    with tenant_session(context_for(tenant_a)) as session:
        titles = session.scalars(select(Ticket.title)).all()
        assert titles == ["alpha ticket"]
        # Naming the other tenant's row by primary key does not help.
        assert session.get(Ticket, b_ticket) is None


def test_cross_tenant_read_by_explicit_tenant_id_predicate(tenant_a, tenant_b, user_factory):
    """The obvious attack: ask for the other tenant by id.

    The policy is ANDed with whatever the caller writes, so a predicate naming
    tenant B under tenant A's context resolves to false. There is no query text
    that can widen it.
    """
    b_user = user_factory(tenant_b, "b@bravo.test")
    _seed_ticket(tenant_b, b_user, 1, "bravo ticket")

    with tenant_session(context_for(tenant_a)) as session:
        rows = session.scalars(select(Ticket).where(Ticket.tenant_id == tenant_b)).all()
    assert rows == []


def test_raw_sql_cannot_escape_the_policy(tenant_a, tenant_b, user_factory):
    """§4.2: raw SQL is the exit every ORM leaves open, which is exactly why the
    filter lives in the database and not in the repository."""
    b_user = user_factory(tenant_b, "b@bravo.test")
    _seed_ticket(tenant_b, b_user, 1, "bravo ticket")

    with tenant_session(context_for(tenant_a)) as session:
        rows = session.execute(text("SELECT title FROM ticket")).all()
        assert rows == []
        rows = session.execute(
            text("SELECT title FROM ticket WHERE tenant_id = :t"), {"t": tenant_b}
        ).all()
        assert rows == []


def test_cross_tenant_read_is_blocked_on_every_tenant_scoped_table(
    tenant_a, tenant_b, user_factory
):
    """Spot-check breadth, not just tickets: a policy applied to 28 tables and
    got wrong on one is the failure mode worth catching."""
    b_user = user_factory(tenant_b, "b@bravo.test")
    with tenant_session(context_for(tenant_b)) as session:
        session.add_all(
            [
                Label(tenant_id=tenant_b, name="bravo-label"),
                Log(tenant_id=tenant_b, author_id=b_user, title="bravo log", body="x"),
            ]
        )
        session.commit()

    with tenant_session(context_for(tenant_a)) as session:
        assert session.scalars(select(User.email)).all() == []
        assert session.scalars(select(Label.name)).all() == []
        assert session.scalars(select(Log.title)).all() == []


# -------------------------------------------------------------------------- write


def test_insert_stamped_with_another_tenant_is_refused(tenant_a, tenant_b, user_factory):
    """The ``WITH CHECK`` half of the policy.

    Without it a caller under tenant A could insert rows *into* tenant B —
    invisible to A afterwards, and therefore never noticed by A's own tests.
    """
    with tenant_session(context_for(tenant_a)) as session:
        session.add(Label(tenant_id=tenant_b, name="planted"))
        with pytest.raises(Exception) as excinfo:
            session.commit()
    assert "row-level security" in str(excinfo.value).lower()


def test_update_cannot_move_a_row_to_another_tenant(tenant_a, tenant_b, user_factory):
    a_user = user_factory(tenant_a, "a@alpha.test")
    ticket_id = _seed_ticket(tenant_a, a_user, 1, "alpha ticket")

    with tenant_session(context_for(tenant_a)) as session:
        with pytest.raises(Exception) as excinfo:
            session.execute(
                update(Ticket).where(Ticket.id == ticket_id).values(tenant_id=tenant_b)
            )
            session.commit()
    assert "row-level security" in str(excinfo.value).lower()


def test_update_of_another_tenants_row_touches_nothing(tenant_a, tenant_b, user_factory):
    """No error here, and that is correct: the row is *invisible*, so the UPDATE
    matches zero rows. The test asserts the row is genuinely unchanged rather
    than trusting the rowcount."""
    b_user = user_factory(tenant_b, "b@bravo.test")
    b_ticket = _seed_ticket(tenant_b, b_user, 1, "bravo ticket")

    with tenant_session(context_for(tenant_a)) as session:
        result = session.execute(
            update(Ticket).where(Ticket.id == b_ticket).values(title="owned")
        )
        session.commit()
        assert result.rowcount == 0

    with tenant_session(context_for(tenant_b)) as session:
        assert session.get(Ticket, b_ticket).title == "bravo ticket"


def test_delete_of_another_tenants_row_touches_nothing(tenant_a, tenant_b, user_factory):
    b_user = user_factory(tenant_b, "b@bravo.test")
    b_ticket = _seed_ticket(tenant_b, b_user, 1, "bravo ticket")

    with tenant_session(context_for(tenant_a)) as session:
        result = session.execute(text("DELETE FROM ticket WHERE id = :i"), {"i": b_ticket})
        session.commit()
        assert result.rowcount == 0

    with tenant_session(context_for(tenant_b)) as session:
        assert session.get(Ticket, b_ticket) is not None


def test_per_tenant_ticket_number_is_unique_within_a_tenant_only(
    tenant_a, tenant_b, user_factory
):
    """TKT-9 / MT-4: ``RL-1`` exists in every tenant, and must.

    Worth an explicit test because the naive unique index on ``number`` alone
    would pass every single-tenant test and break the second tenant.
    """
    a_user = user_factory(tenant_a, "a@alpha.test")
    b_user = user_factory(tenant_b, "b@bravo.test")
    _seed_ticket(tenant_a, a_user, 1, "alpha RL-1")
    _seed_ticket(tenant_b, b_user, 1, "bravo RL-1")

    with tenant_session(context_for(tenant_a)) as session:
        with pytest.raises(Exception) as excinfo:
            session.add(
                Ticket(tenant_id=tenant_a, number=1, type=TicketType.TASK, title="duplicate")
            )
            session.commit()
    assert "uq_ticket_tenant_id_number" in str(excinfo.value)


def test_app_role_cannot_read_through_a_second_connection(tenant_a, tenant_b, user_factory):
    """Connection reuse is the shape of leak that survives every unit test.

    Establish tenant A's context on a pooled connection, return it, then use the
    pool again with tenant B and confirm A's rows are not visible.
    """
    a_user = user_factory(tenant_a, "a@alpha.test")
    _seed_ticket(tenant_a, a_user, 1, "alpha ticket")

    engine = app_engine()
    for _ in range(5):
        with tenant_session(context_for(tenant_b)) as session:
            assert session.scalars(select(Ticket.title)).all() == []
    engine.dispose()


# ------------------------------------------------------- referential integrity


def test_cannot_reference_another_tenants_row(tenant_a, tenant_b, user_factory):
    """The gap RLS does not cover, and the reason every FK here is composite.

    PostgreSQL runs foreign-key checks with policies bypassed. With a plain
    ``FOREIGN KEY (user_id) REFERENCES "user"(id)``, this insert succeeds:
    tenant A adds tenant B's user to tenant A's space. Nothing leaks on read —
    the join finds nothing, because the user stays invisible — so a read-only
    negative suite calls it clean.
    """
    b_user = user_factory(tenant_b, "b@bravo.test")
    space = Space(tenant_id=tenant_a, name="alpha space")
    with tenant_session(context_for(tenant_a)) as session:
        session.add(space)
        session.commit()
        space_id = space.id

    with tenant_session(context_for(tenant_a)) as session:
        session.add(SpaceMember(tenant_id=tenant_a, space_id=space_id, user_id=b_user))
        with pytest.raises(Exception) as excinfo:
            session.commit()
    assert "foreign key" in str(excinfo.value).lower()


def test_another_tenants_delete_cannot_cascade_into_ours(tenant_a, tenant_b, user_factory):
    """The other half, and the more damaging one.

    With single-column FKs, tenant B deleting a user would cascade-delete tenant
    A's rows that referenced it — a cross-tenant *write*, executed by a tenant
    who never had permission to touch A's data and would see no sign they had.
    Composite keys make the reference impossible in the first place, so there is
    nothing to cascade.
    """
    a_user = user_factory(tenant_a, "a@alpha.test")
    b_user = user_factory(tenant_b, "b@bravo.test")
    ticket_id = _seed_ticket(tenant_a, a_user, 1, "alpha ticket")

    with tenant_session(context_for(tenant_a)) as session:
        with pytest.raises(Exception) as excinfo:
            session.execute(
                update(Ticket).where(Ticket.id == ticket_id).values(assignee_id=b_user)
            )
            session.commit()
    assert "foreign key" in str(excinfo.value).lower()

    with tenant_session(context_for(tenant_b)) as session:
        session.execute(text('DELETE FROM "user" WHERE id = :i'), {"i": b_user})
        session.commit()

    with tenant_session(context_for(tenant_a)) as session:
        assert session.get(Ticket, ticket_id) is not None


def test_every_cross_table_reference_is_tenant_scoped():
    """Structural check, so a new model cannot reintroduce the gap quietly.

    Every foreign key must either be the ``tenant_id -> tenant.id`` root
    reference or a two-column key that includes ``tenant_id``.
    """
    from relay.infra.db.models import Base

    offenders: list[str] = []
    for table in Base.metadata.tables.values():
        for fk in table.foreign_key_constraints:
            columns = {c.name for c in fk.columns}
            if columns == {"tenant_id"}:
                continue  # the root reference to tenant.id
            if "tenant_id" not in columns:
                offenders.append(f"{table.name}.{sorted(columns)} -> {fk.referred_table.name}")
    assert not offenders, (
        "foreign keys that can cross a tenant boundary:\n  " + "\n  ".join(offenders)
    )
