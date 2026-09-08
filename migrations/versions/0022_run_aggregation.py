"""Add explicit scientific run grouping and evidence attachment views.

Revision ID: 0022_run_aggregation
Revises: 0021_autoresearch_scorer_invocation_ledger
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_run_aggregation"
down_revision = "0021_autoresearch_scorer_invocation_ledger"
branch_labels = None
depends_on = None


PHASE_CHECK = (
    "phase_code IN ('generation','score_all','challenger','qd_lineage',"
    "'boltz','rosetta','md','pool_s','unclassified')"
)


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _json() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # Fail quickly instead of waiting behind active AutoResearch writers.
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL idle_in_transaction_session_timeout = '15s'")
    op.create_table(
        "scientific_roots",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("root_key", sa.String(128), nullable=False),
        sa.Column("root_run_id", _uuid(), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["root_run_id"], ["experiment_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("root_key", name="uq_scientific_root_key"),
        sa.UniqueConstraint("root_run_id", name="uq_scientific_root_run"),
    )
    op.create_table(
        "scientific_run_groups",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("group_key", sa.String(128), nullable=False),
        sa.Column("scientific_root_id", _uuid(), nullable=False),
        sa.Column("root_run_id", _uuid(), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["scientific_root_id"], ["scientific_roots.id"]),
        sa.ForeignKeyConstraint(["root_run_id"], ["experiment_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_key", name="uq_scientific_run_group_key"),
        sa.UniqueConstraint("root_run_id", name="uq_scientific_run_group_root"),
    )

    op.add_column("experiment_runs", sa.Column("run_group_id", _uuid(), nullable=True))
    op.add_column("experiment_runs", sa.Column("root_run_id", _uuid(), nullable=True))
    op.add_column("experiment_runs", sa.Column("phase_code", sa.String(32), nullable=True))
    op.add_column("experiment_runs", sa.Column("phase_ordinal", sa.Integer(), nullable=True))
    op.add_column("experiment_runs", sa.Column("aggregation_basis", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_experiment_run_group",
        "experiment_runs",
        "scientific_run_groups",
        ["run_group_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_experiment_run_root",
        "experiment_runs",
        "experiment_runs",
        ["root_run_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_experiment_run_phase_pair",
        "experiment_runs",
        "(phase_code IS NULL AND phase_ordinal IS NULL) OR "
        f"(phase_code IS NOT NULL AND phase_ordinal >= 0 AND {PHASE_CHECK})",
    )
    op.create_index("ix_experiment_run_group", "experiment_runs", ["run_group_id"])
    op.create_index("ix_experiment_run_root", "experiment_runs", ["root_run_id"])

    op.create_table(
        "run_lineage_edges",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("parent_run_id", _uuid(), nullable=False),
        sa.Column("child_run_id", _uuid(), nullable=False),
        sa.Column("relation_type", sa.String(64), nullable=False),
        sa.Column("relation_ordinal", sa.Integer(), nullable=False),
        sa.Column("binding_basis", sa.String(64), nullable=False),
        sa.Column("edge_sha256", sa.String(64), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("parent_run_id <> child_run_id", name="ck_run_lineage_not_self"),
        sa.CheckConstraint("relation_ordinal > 0", name="ck_run_lineage_positive_ordinal"),
        sa.ForeignKeyConstraint(["parent_run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["child_run_id"], ["experiment_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("edge_sha256", name="uq_run_lineage_edge_sha256"),
        sa.UniqueConstraint(
            "parent_run_id",
            "child_run_id",
            "relation_type",
            name="uq_run_lineage_identity",
        ),
    )
    op.create_index("ix_run_lineage_child", "run_lineage_edges", ["child_run_id"])
    op.execute(
        """
        CREATE FUNCTION validate_run_lineage_edge_v1() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (
            WITH RECURSIVE descendants(id,path) AS (
              SELECT NEW.child_run_id,ARRAY[NEW.child_run_id]
              UNION ALL
              SELECT edge.child_run_id,d.path||edge.child_run_id
              FROM descendants d JOIN run_lineage_edges edge ON edge.parent_run_id=d.id
              WHERE NOT edge.child_run_id=ANY(d.path)
            )
            SELECT 1 FROM descendants WHERE id=NEW.parent_run_id
          ) THEN RAISE EXCEPTION 'run lineage edge would create a cycle';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_validate_run_lineage_edge_v1
        BEFORE INSERT ON run_lineage_edges
        FOR EACH ROW EXECUTE FUNCTION validate_run_lineage_edge_v1()
        """
    )

    op.create_table(
        "run_evidence_attachments",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("subject_run_id", _uuid(), nullable=False),
        sa.Column("subject_candidate_id", _uuid(), nullable=True),
        sa.Column("producer_run_id", _uuid(), nullable=False),
        sa.Column("tool_call_id", _uuid(), nullable=False),
        sa.Column("phase_code", sa.String(32), nullable=False),
        sa.Column("phase_order", sa.Integer(), nullable=False),
        sa.Column("evidence_ordinal", sa.Integer(), nullable=False),
        sa.Column("evidence_role", sa.String(64), nullable=False),
        sa.Column("binding_basis", sa.String(64), nullable=False),
        sa.Column("attachment_sha256", sa.String(64), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(PHASE_CHECK, name="ck_run_evidence_attachment_phase"),
        sa.CheckConstraint("phase_order >= 0", name="ck_run_evidence_phase_order"),
        sa.CheckConstraint("evidence_ordinal > 0", name="ck_run_evidence_positive_ordinal"),
        sa.ForeignKeyConstraint(["subject_run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["subject_candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["producer_run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["tool_call_id"], ["tool_calls.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attachment_sha256", name="uq_run_evidence_attachment_sha256"),
    )
    op.create_index(
        "ix_run_evidence_subject_timeline",
        "run_evidence_attachments",
        ["subject_run_id", "phase_order", "evidence_ordinal"],
    )
    op.create_index(
        "ix_run_evidence_candidate",
        "run_evidence_attachments",
        ["subject_candidate_id", "phase_order"],
    )
    op.create_index("ix_run_evidence_producer", "run_evidence_attachments", ["producer_run_id"])

    op.create_table(
        "run_invalidation_events",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("run_id", _uuid(), nullable=False),
        sa.Column("event_ordinal", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decision_id", _uuid(), nullable=True),
        sa.Column("event_sha256", sa.String(64), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("event_ordinal > 0", name="ck_run_invalidation_positive_ordinal"),
        sa.CheckConstraint(
            "event_type IN ('invalidate','reinstate')", name="ck_run_invalidation_event_type"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["decision_id"], ["agent_decisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_sha256", name="uq_run_invalidation_event_sha256"),
        sa.UniqueConstraint("run_id", "event_ordinal", name="uq_run_invalidation_event_ordinal"),
    )

    op.create_table(
        "evidence_invalidation_events",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("subject_run_id", _uuid(), nullable=False),
        sa.Column("subject_candidate_id", _uuid(), nullable=True),
        sa.Column("record_kind", sa.String(64), nullable=False),
        sa.Column("record_id", _uuid(), nullable=False),
        sa.Column("event_ordinal", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decision_id", _uuid(), nullable=True),
        sa.Column("event_sha256", sa.String(64), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("event_ordinal > 0", name="ck_evidence_invalidation_positive_ordinal"),
        sa.CheckConstraint(
            "event_type IN ('invalidate','reinstate')",
            name="ck_evidence_invalidation_event_type",
        ),
        sa.ForeignKeyConstraint(["subject_run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["subject_candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["decision_id"], ["agent_decisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_sha256", name="uq_evidence_invalidation_event_sha256"),
        sa.UniqueConstraint(
            "record_kind",
            "record_id",
            "event_ordinal",
            name="uq_evidence_invalidation_event_ordinal",
        ),
    )
    op.execute(
        """
        CREATE FUNCTION reject_aggregation_mutation_v1() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'aggregation evidence is append-only';
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_run_lineage_append_only_v1
        BEFORE UPDATE OR DELETE ON run_lineage_edges
        FOR EACH ROW EXECUTE FUNCTION reject_aggregation_mutation_v1()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_run_evidence_attachment_append_only_v1
        BEFORE UPDATE OR DELETE ON run_evidence_attachments
        FOR EACH ROW EXECUTE FUNCTION reject_aggregation_mutation_v1()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_run_invalidation_append_only_v1
        BEFORE UPDATE OR DELETE ON run_invalidation_events
        FOR EACH ROW EXECUTE FUNCTION reject_aggregation_mutation_v1()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_evidence_invalidation_append_only_v1
        BEFORE UPDATE OR DELETE ON evidence_invalidation_events
        FOR EACH ROW EXECUTE FUNCTION reject_aggregation_mutation_v1()
        """
    )

    op.execute(
        """
        CREATE FUNCTION validate_run_evidence_attachment_v1() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE candidate_run uuid; call_run uuid; expected_order integer;
        BEGIN
          IF NEW.subject_candidate_id IS NOT NULL THEN
            SELECT run_id INTO candidate_run FROM candidates WHERE id=NEW.subject_candidate_id;
            IF candidate_run IS DISTINCT FROM NEW.subject_run_id THEN
              RAISE EXCEPTION 'subject candidate does not belong to subject run';
            END IF;
          END IF;
          SELECT run_id INTO call_run FROM tool_calls WHERE id=NEW.tool_call_id;
          IF call_run IS DISTINCT FROM NEW.producer_run_id THEN
            RAISE EXCEPTION 'tool call does not belong to producer run';
          END IF;
          expected_order := CASE NEW.phase_code
            WHEN 'generation' THEN 10 WHEN 'score_all' THEN 20
            WHEN 'challenger' THEN 30 WHEN 'qd_lineage' THEN 40
            WHEN 'boltz' THEN 50 WHEN 'rosetta' THEN 60
            WHEN 'md' THEN 70 WHEN 'pool_s' THEN 80 ELSE 900 END;
          IF NEW.phase_order <> expected_order THEN
            RAISE EXCEPTION 'phase order does not match frozen phase code';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_validate_run_evidence_attachment_v1
        BEFORE INSERT OR UPDATE ON run_evidence_attachments
        FOR EACH ROW EXECUTE FUNCTION validate_run_evidence_attachment_v1()
        """
    )

    op.execute(
        """
        CREATE VIEW scientific_evidence_mismatches_v1 AS
        SELECT e.id evaluation_id,e.subject_run_id,c.run_id candidate_run_id,
          tc.run_id producer_run_id,e.candidate_id,e.tool_call_id,
          'evaluation_subject_run_differs_from_candidate_run'::text reason
        FROM evaluations e
        JOIN candidates c ON c.id=e.candidate_id
        JOIN tool_calls tc ON tc.id=e.tool_call_id
        WHERE e.subject_run_id IS NOT NULL AND e.subject_run_id<>c.run_id
        """
    )

    op.execute(
        """
        CREATE VIEW scientific_run_lineage_v1 AS
        SELECT parent_run_id,child_run_id,relation_type,relation_ordinal,
          binding_basis,edge_sha256,true persisted
        FROM run_lineage_edges
        UNION ALL
        SELECT r.parent_run_id,r.id,'parent_run'::varchar(64),1,
          'experiment_runs.parent_run_id_live'::varchar(64),NULL::varchar(64),false
        FROM experiment_runs r
        WHERE r.parent_run_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM run_lineage_edges edge
          WHERE edge.parent_run_id=r.parent_run_id AND edge.child_run_id=r.id
            AND edge.relation_type='parent_run'
        )
        """
    )

    op.execute(
        """
        CREATE VIEW scientific_operational_evidence_v1 AS
        SELECT attachment.subject_run_id,attachment.subject_candidate_id,
          attachment.producer_run_id,attachment.tool_call_id,
          attachment.phase_code,attachment.phase_order,attachment.evidence_ordinal,
          attachment.evidence_role,attachment.binding_basis,
          tc.tool_name,tc.tool_version,tc.status tool_status,
          ea.role artifact_role,a.id artifact_id,a.sha256 artifact_sha256,
          a.storage_uri,a.media_type
        FROM run_evidence_attachments attachment
        JOIN tool_calls tc ON tc.id=attachment.tool_call_id
        LEFT JOIN evidence_artifacts ea ON ea.tool_call_id=tc.id
        LEFT JOIN artifacts a ON a.id=ea.artifact_id
        """
    )

    op.execute(
        """
        CREATE VIEW scientific_candidate_evidence_v1 AS
        SELECT
          COALESCE(r.run_group_id, c.run_id) AS run_group_id,
          COALESCE(r.root_run_id, c.run_id) AS root_run_id,
          c.run_id AS subject_run_id,
          c.id AS candidate_id,
          tc.run_id AS producer_run_id,
          e.tool_call_id,
          'evaluation'::text AS record_kind,
          e.id AS record_id,
          CASE e.evidence_role
            WHEN 'primary' THEN 'score_all' WHEN 'hard_gate' THEN 'score_all'
            WHEN 'challenger' THEN 'challenger' WHEN 'shadow' THEN 'challenger'
            WHEN 'structure' THEN 'unclassified' WHEN 'md' THEN 'md'
            ELSE 'unclassified' END AS phase_code,
          CASE e.evidence_role
            WHEN 'primary' THEN 20 WHEN 'hard_gate' THEN 20
            WHEN 'challenger' THEN 30 WHEN 'shadow' THEN 30
            WHEN 'md' THEN 70 ELSE 900 END AS phase_order,
          row_number() OVER (
            PARTITION BY c.run_id, c.id,
              CASE e.evidence_role WHEN 'primary' THEN 20 WHEN 'hard_gate' THEN 20
                WHEN 'challenger' THEN 30 WHEN 'shadow' THEN 30
                WHEN 'md' THEN 70 ELSE 900 END
            ORDER BY e.metric_name, COALESCE(e.model_release_key,''), e.tool_call_id, e.id
          )::bigint AS evidence_ordinal,
          e.metric_name AS evidence_name,
          e.status AS evidence_status,
          e.numeric_value,
          e.text_value,
          e.unit,
          COALESCE(e.model_release_key, tc.tool_version) AS model_release_key,
          CASE
            WHEN e.subject_run_id=c.run_id THEN 'evaluation_subject_run'
            WHEN e.subject_run_id IS NULL THEN 'candidate_foreign_key'
            ELSE 'conflicting_subject_run'
          END AS association_basis,
          (e.subject_run_id IS NOT NULL AND e.subject_run_id<>c.run_id) AS unresolved,
          e.created_at AS recorded_at
        FROM evaluations e
        JOIN candidates c ON c.id=e.candidate_id
        JOIN experiment_runs r ON r.id=c.run_id
        JOIN tool_calls tc ON tc.id=e.tool_call_id

        UNION ALL

        SELECT
          COALESCE(r.run_group_id, c.run_id), COALESCE(r.root_run_id, c.run_id),
          c.run_id, c.id, tc.run_id, s.tool_call_id,
          'structure_record'::text, s.id,
          CASE s.evidence_kind WHEN 'boltz_pose' THEN 'boltz' ELSE 'rosetta' END,
          CASE s.evidence_kind WHEN 'boltz_pose' THEN 50 ELSE 60 END,
          CASE WHEN s.decoy_ordinal < 0 THEN 1 ELSE s.decoy_ordinal + 1 END,
          s.evidence_kind, tc.status, NULL::double precision, NULL::text,
          NULL::varchar(64), tc.tool_version,
          'structure_candidate_foreign_key'::text, false, s.created_at
        FROM multitarget_structure_evidence_records s
        JOIN candidates c ON c.id=s.candidate_id
        JOIN experiment_runs r ON r.id=c.run_id
        JOIN tool_calls tc ON tc.id=s.tool_call_id
        """
    )

    op.execute(
        """
        CREATE VIEW scientific_evidence_validity_v1 AS
        SELECT ce.*,
          COALESCE(last_event.event_type='invalidate',false) is_explicitly_invalidated
        FROM scientific_candidate_evidence_v1 ce
        LEFT JOIN LATERAL (
          SELECT event_type FROM evidence_invalidation_events ie
          WHERE ie.record_kind=ce.record_kind AND ie.record_id=ce.record_id
          ORDER BY ie.event_ordinal DESC LIMIT 1
        ) last_event ON true
        """
    )

    op.execute(
        """
        CREATE VIEW scientific_run_timeline_v1 AS
        SELECT COALESCE(r.run_group_id,r.id) run_group_id,
          COALESCE(r.root_run_id,r.id) root_run_id, r.id subject_run_id,
          c.id candidate_id, r.id producer_run_id, c.generator_call_id tool_call_id,
          'candidate'::text record_kind, c.id record_id, 'generation'::text phase_code,
          10 phase_order,
          row_number() OVER (PARTITION BY c.run_id ORDER BY c.generation,
            c.proposal_rank NULLS LAST,c.id)::bigint scientific_ordinal,
          c.status record_status, false is_explicitly_invalidated, c.created_at recorded_at
        FROM candidates c JOIN experiment_runs r ON r.id=c.run_id
        UNION ALL
        SELECT run_group_id,root_run_id,subject_run_id,candidate_id,producer_run_id,
          tool_call_id,record_kind,record_id,phase_code,phase_order,evidence_ordinal,
          evidence_status,is_explicitly_invalidated,recorded_at
        FROM scientific_evidence_validity_v1
        UNION ALL
        SELECT COALESCE(r.run_group_id,r.id),COALESCE(r.root_run_id,r.id),r.id,NULL::uuid,
          r.id,NULL::uuid,'autoresearch_action'::text,a.id,'qd_lineage'::text,40,
          row_number() OVER (PARTITION BY a.run_id ORDER BY a.iteration_no,
          a.action_ordinal,a.id)::bigint,'persisted'::text,false,a.created_at
        FROM autoresearch_actions a JOIN experiment_runs r ON r.id=a.run_id
        """
    )

    op.execute(
        """
        CREATE VIEW scientific_run_aggregate_v1 AS
        WITH c AS (SELECT run_id,count(*) candidate_count FROM candidates GROUP BY run_id),
        e AS (SELECT subject_run_id,count(*) evidence_count,
          count(*) FILTER (WHERE unresolved) unresolved_evidence_count,
          count(*) FILTER (WHERE is_explicitly_invalidated) invalidated_evidence_count,
          count(DISTINCT phase_code) FILTER (WHERE phase_code<>'unclassified') phase_count
          FROM scientific_evidence_validity_v1 GROUP BY subject_run_id),
        tc AS (SELECT run_id,count(*) tool_call_count FROM tool_calls GROUP BY run_id),
        a AS (SELECT run_id,count(*) action_count FROM autoresearch_actions GROUP BY run_id),
        ri AS (SELECT DISTINCT ON (run_id) run_id,event_type FROM run_invalidation_events
          ORDER BY run_id,event_ordinal DESC)
        SELECT COALESCE(r.run_group_id,r.id) run_group_id,
          COALESCE(r.root_run_id,r.id) root_run_id,r.id run_id,r.parent_run_id,
          r.phase_code,r.phase_ordinal,r.status,
          COALESCE(c.candidate_count,0) candidate_count,
          COALESCE(e.evidence_count,0) evidence_count,
          COALESCE(e.unresolved_evidence_count,0) unresolved_evidence_count,
          COALESCE(e.invalidated_evidence_count,0) invalidated_evidence_count,
          COALESCE(ri.event_type='invalidate',false) run_is_explicitly_invalidated,
          COALESCE(e.phase_count,0) represented_phase_count,
          COALESCE(tc.tool_call_count,0) producer_tool_call_count,
          COALESCE(a.action_count,0) autoresearch_action_count
        FROM experiment_runs r
        LEFT JOIN c ON c.run_id=r.id LEFT JOIN e ON e.subject_run_id=r.id
        LEFT JOIN tc ON tc.run_id=r.id LEFT JOIN a ON a.run_id=r.id
        LEFT JOIN ri ON ri.run_id=r.id
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW scientific_run_aggregate_v1")
    op.execute("DROP VIEW scientific_run_timeline_v1")
    op.execute("DROP VIEW scientific_evidence_validity_v1")
    op.execute("DROP VIEW scientific_candidate_evidence_v1")
    op.execute("DROP VIEW scientific_operational_evidence_v1")
    op.execute("DROP VIEW scientific_run_lineage_v1")
    op.execute("DROP VIEW scientific_evidence_mismatches_v1")
    op.execute(
        "DROP TRIGGER trg_evidence_invalidation_append_only_v1 "
        "ON evidence_invalidation_events"
    )
    op.execute("DROP TRIGGER trg_run_invalidation_append_only_v1 ON run_invalidation_events")
    op.execute(
        "DROP TRIGGER trg_run_evidence_attachment_append_only_v1 "
        "ON run_evidence_attachments"
    )
    op.execute("DROP TRIGGER trg_run_lineage_append_only_v1 ON run_lineage_edges")
    op.execute("DROP FUNCTION reject_aggregation_mutation_v1()")
    op.drop_table("evidence_invalidation_events")
    op.drop_table("run_invalidation_events")
    op.execute("DROP TRIGGER trg_validate_run_evidence_attachment_v1 ON run_evidence_attachments")
    op.execute("DROP FUNCTION validate_run_evidence_attachment_v1()")
    op.drop_index("ix_run_evidence_producer", table_name="run_evidence_attachments")
    op.drop_index("ix_run_evidence_candidate", table_name="run_evidence_attachments")
    op.drop_index("ix_run_evidence_subject_timeline", table_name="run_evidence_attachments")
    op.drop_table("run_evidence_attachments")
    op.execute("DROP TRIGGER trg_validate_run_lineage_edge_v1 ON run_lineage_edges")
    op.execute("DROP FUNCTION validate_run_lineage_edge_v1()")
    op.drop_index("ix_run_lineage_child", table_name="run_lineage_edges")
    op.drop_table("run_lineage_edges")
    op.drop_index("ix_experiment_run_root", table_name="experiment_runs")
    op.drop_index("ix_experiment_run_group", table_name="experiment_runs")
    op.drop_constraint("ck_experiment_run_phase_pair", "experiment_runs", type_="check")
    op.drop_constraint("fk_experiment_run_root", "experiment_runs", type_="foreignkey")
    op.drop_constraint("fk_experiment_run_group", "experiment_runs", type_="foreignkey")
    op.drop_column("experiment_runs", "aggregation_basis")
    op.drop_column("experiment_runs", "phase_ordinal")
    op.drop_column("experiment_runs", "phase_code")
    op.drop_column("experiment_runs", "root_run_id")
    op.drop_column("experiment_runs", "run_group_id")
    op.drop_table("scientific_run_groups")
    op.drop_table("scientific_roots")
