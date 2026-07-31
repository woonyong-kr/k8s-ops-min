"""rca_reports 목록 조회 projection 컬럼.

Revision ID: 20260708_0610
Revises: 20260708_0525
Create Date: 2026-07-08 06:10:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260708_0610"
down_revision: str | None = "20260708_0525"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            ALTER TABLE rca_reports
                ADD COLUMN IF NOT EXISTS incident_id text,
                ADD COLUMN IF NOT EXISTS cluster_id text,
                ADD COLUMN IF NOT EXISTS symptom text,
                ADD COLUMN IF NOT EXISTS severity text,
                ADD COLUMN IF NOT EXISTS confidence double precision,
                ADD COLUMN IF NOT EXISTS reason text,
                ADD COLUMN IF NOT EXISTS evidence_ref text,
                ADD COLUMN IF NOT EXISTS supporting_evidence jsonb,
                ADD COLUMN IF NOT EXISTS missing_evidence jsonb,
                ADD COLUMN IF NOT EXISTS resource_kind text,
                ADD COLUMN IF NOT EXISTS resource_name text,
                ADD COLUMN IF NOT EXISTS namespace text,
                ADD COLUMN IF NOT EXISTS secondary_symptoms jsonb,
                ADD COLUMN IF NOT EXISTS selected_candidate_id text,
                ADD COLUMN IF NOT EXISTS candidates jsonb,
                ADD COLUMN IF NOT EXISTS supporting_evidence_refs jsonb,
                ADD COLUMN IF NOT EXISTS missing_evidence_checks jsonb
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE rca_reports
            SET
                incident_id = COALESCE(incident_id, NULLIF(payload #>> '{incident,incident_id}', '')),
                cluster_id = COALESCE(cluster_id, NULLIF(payload #>> '{incident,cluster_id}', '')),
                symptom = COALESCE(symptom, NULLIF(payload #>> '{incident,symptom}', '')),
                severity = COALESCE(severity, NULLIF(payload #>> '{incident,severity}', '')),
                confidence = COALESCE(
                    confidence,
                    CASE
                        WHEN (payload #>> '{rca_detail,confidence}') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN (payload #>> '{rca_detail,confidence}')::double precision
                        ELSE NULL
                    END
                ),
                reason = COALESCE(reason, NULLIF(payload #>> '{rca_detail,reason}', '')),
                evidence_ref = COALESCE(evidence_ref, NULLIF(payload #>> '{evidence_ref}', '')),
                supporting_evidence = COALESCE(
                    supporting_evidence,
                    payload #> '{rca_detail,supporting_evidence}',
                    '[]'::jsonb
                ),
                missing_evidence = COALESCE(
                    missing_evidence,
                    payload #> '{rca_detail,missing_evidence}',
                    '[]'::jsonb
                ),
                resource_kind = COALESCE(
                    resource_kind,
                    NULLIF(payload #>> '{incident,resource_kind}', '')
                ),
                resource_name = COALESCE(
                    resource_name,
                    NULLIF(payload #>> '{incident,resource_name}', '')
                ),
                namespace = COALESCE(namespace, NULLIF(payload #>> '{incident,namespace}', '')),
                secondary_symptoms = COALESCE(
                    secondary_symptoms,
                    payload #> '{incident,secondary_symptoms}',
                    '[]'::jsonb
                ),
                selected_candidate_id = COALESCE(
                    selected_candidate_id,
                    NULLIF(payload #>> '{rca_detail,selected_candidate_id}', '')
                ),
                candidates = COALESCE(candidates, '[]'::jsonb),
                supporting_evidence_refs = COALESCE(
                    supporting_evidence_refs,
                    payload #> '{rca_detail,supporting_evidence_refs}',
                    '[]'::jsonb
                ),
                missing_evidence_checks = COALESCE(
                    missing_evidence_checks,
                    payload #> '{rca_detail,missing_evidence_checks}',
                    '[]'::jsonb
                )
            WHERE payload IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            ALTER TABLE rca_reports
                DROP COLUMN IF EXISTS missing_evidence_checks,
                DROP COLUMN IF EXISTS supporting_evidence_refs,
                DROP COLUMN IF EXISTS candidates,
                DROP COLUMN IF EXISTS selected_candidate_id,
                DROP COLUMN IF EXISTS secondary_symptoms,
                DROP COLUMN IF EXISTS namespace,
                DROP COLUMN IF EXISTS resource_name,
                DROP COLUMN IF EXISTS resource_kind,
                DROP COLUMN IF EXISTS missing_evidence,
                DROP COLUMN IF EXISTS supporting_evidence,
                DROP COLUMN IF EXISTS evidence_ref,
                DROP COLUMN IF EXISTS reason,
                DROP COLUMN IF EXISTS confidence,
                DROP COLUMN IF EXISTS severity,
                DROP COLUMN IF EXISTS symptom,
                DROP COLUMN IF EXISTS cluster_id,
                DROP COLUMN IF EXISTS incident_id
            """
        )
    )
