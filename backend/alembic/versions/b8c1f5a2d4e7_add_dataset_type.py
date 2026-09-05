"""add explicit evaluation and demo dataset markers

Revision ID: b8c1f5a2d4e7
Revises: f400aa69edac
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c1f5a2d4e7"
down_revision: Union[str, None] = "f400aa69edac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("dataset_type", sa.String(), nullable=False, server_default="EVALUATION"),
    )
    op.add_column(
        "payments",
        sa.Column("dataset_type", sa.String(), nullable=False, server_default="EVALUATION"),
    )
    op.add_column(
        "revenue_risk_cases",
        sa.Column("dataset_type", sa.String(), nullable=False, server_default="EVALUATION"),
    )
    op.create_index("ix_customers_dataset_type", "customers", ["dataset_type"], unique=False)
    op.create_index("ix_payments_dataset_type", "payments", ["dataset_type"], unique=False)
    op.create_index("ix_revenue_risk_cases_dataset_type", "revenue_risk_cases", ["dataset_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_revenue_risk_cases_dataset_type", table_name="revenue_risk_cases")
    op.drop_index("ix_payments_dataset_type", table_name="payments")
    op.drop_index("ix_customers_dataset_type", table_name="customers")
    op.drop_column("customers", "dataset_type")
    op.drop_column("revenue_risk_cases", "dataset_type")
    op.drop_column("payments", "dataset_type")