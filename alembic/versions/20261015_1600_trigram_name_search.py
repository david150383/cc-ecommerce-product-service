"""trigram index on product names: typo- and prefix-tolerant search

Revision ID: 3b7c1d9e2f40
Revises: e84d4ca4fca0
Create Date: 2026-10-15 16:00:00

pg_trgm is a "trusted" extension (PostgreSQL 13+, incl. RDS): the database owner (product_svc) may
create it, no superuser needed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3b7c1d9e2f40"
down_revision: str | Sequence[str] | None = "e84d4ca4fca0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_products_name_trgm",
        "products",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_products_name_trgm", table_name="products", postgresql_using="gin")
    # The extension stays: other objects may use it, and dropping it needs more than this migration knows.
