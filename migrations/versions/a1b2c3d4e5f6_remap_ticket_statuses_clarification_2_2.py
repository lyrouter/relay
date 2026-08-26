"""Remap ticket statuses to clarification 2.2 graph.

Replaces ``todo / in_progress / in_review / done / blocked / wont_fix`` with
``new / assign / working / resolved / reopen / closed`` on the same ``ticket``
and ``ticket_status_history`` columns. Storage is SQLAlchemy enum **names**
(``TODO`` → ``NEW``, …) because ``Enum(..., native_enum=False)`` persists names.

Mapping chosen so reopenable work lands on ``RESOLVED`` (not terminal
``CLOSED``), abandoned work on ``CLOSED``, and in-flight / blocked work on
``WORKING``. There is no peer for the old ``in_review`` or ``blocked``.

Revision ID: a1b2c3d4e5f6
Revises: e8c3d91a4b27
Create Date: 2026-08-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "e8c3d91a4b27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Old name → new name. Applied to ticket.status and both history columns.
_FORWARD = {
    "TODO": "NEW",
    "IN_PROGRESS": "WORKING",
    "IN_REVIEW": "WORKING",
    "DONE": "RESOLVED",
    "BLOCKED": "WORKING",
    "WONT_FIX": "CLOSED",
}

#: Best-effort reverse. ``WORKING`` and multi-source collapses cannot restore
#: ``IN_REVIEW`` / ``BLOCKED``; those become ``IN_PROGRESS``.
_REVERSE = {
    "NEW": "TODO",
    "ASSIGN": "TODO",
    "WORKING": "IN_PROGRESS",
    "RESOLVED": "DONE",
    "REOPEN": "TODO",
    "CLOSED": "WONT_FIX",
}


def upgrade() -> None:
    # FORCE RLS binds the table owner too. Temporarily drop FORCE so this
    # rewrite touches every tenant; restore FORCE before leaving.
    for table in ("ticket", "ticket_status_history"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    for table, column in (
        ("ticket", "status"),
        ("ticket_status_history", "from_status"),
        ("ticket_status_history", "to_status"),
    ):
        for old, new in _FORWARD.items():
            op.execute(
                f"UPDATE {table} SET {column} = '{new}' WHERE {column} = '{old}'"
            )
    for table in ("ticket", "ticket_status_history"):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in ("ticket", "ticket_status_history"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    for table, column in (
        ("ticket", "status"),
        ("ticket_status_history", "from_status"),
        ("ticket_status_history", "to_status"),
    ):
        for new, old in _REVERSE.items():
            op.execute(
                f"UPDATE {table} SET {column} = '{old}' WHERE {column} = '{new}'"
            )
    for table in ("ticket", "ticket_status_history"):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
