"""Support-ticket category + service-token attachment uploads.

Gateway support tickets are sourced in the control plane. Relay keeps a copy
for the agent workbench, which means the category the tenant picked has to
survive sync as a column — a label is too easy to rename away — and a service
token has to be able to attach the files that came with the ticket.

``attachment.uploaded_by`` becomes nullable for that second half: a service
principal has no user row (S-10), and the previous NOT NULL + user FK refused
the upload before the blob store was even asked.

Revision ID: e8c3d91a4b27
Revises: 66a78ea01295
Create Date: 2026-08-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8c3d91a4b27"
down_revision: Union[str, Sequence[str], None] = "66a78ea01295"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ticket", sa.Column("category", sa.String(length=32), nullable=True))
    op.create_index("ix_ticket_tenant_id_category", "ticket", ["tenant_id", "category"])
    op.alter_column("attachment", "uploaded_by", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    op.alter_column("attachment", "uploaded_by", existing_type=sa.Uuid(), nullable=False)
    op.drop_index("ix_ticket_tenant_id_category", table_name="ticket")
    op.drop_column("ticket", "category")
