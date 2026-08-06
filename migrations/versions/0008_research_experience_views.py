"""Add target-explicit, workflow-independent research experience views."""

from alembic import op

revision = "0008_research_experience_views"
down_revision = "0007_agent_decision_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW research_experience_records_v1 AS
        SELECT
            t.id AS target_id,
            t.name AS target_name,
            t.accession AS target_accession,
            t.sequence_sha256 AS target_sequence_sha256,
            r.id AS run_id,
            r.spec_sha256 AS run_spec_sha256,
            r.status AS run_status,
            c.id AS candidate_id,
            c.sequence AS peptide_sequence,
            c.sequence_sha256 AS peptide_sequence_sha256,
            c.generation,
            'evaluation'::text AS record_kind,
            e.id AS record_id,
            e.metric_name AS record_name,
            e.status AS record_status,
            e.tool_call_id,
            NULL::uuid AS decision_id,
            NULL::uuid AS artifact_id,
            e.numeric_value,
            e.text_value,
            e.unit,
            tc.output_sha256 AS source_sha256,
            jsonb_build_object(
                'out_of_domain', e.out_of_domain,
                'limitations', e.limitations_json,
                'raw', e.raw_json,
                'tool_name', tc.tool_name,
                'tool_version', tc.tool_version,
                'weights_sha256', tc.weights_sha256,
                'environment_sha256', tc.environment_sha256
            ) AS evidence_json,
            e.created_at AS recorded_at
        FROM evaluations e
        JOIN candidates c ON c.id = e.candidate_id
        JOIN experiment_runs r ON r.id = c.run_id
        JOIN targets t ON t.id = r.target_id
        JOIN tool_calls tc ON tc.id = e.tool_call_id

        UNION ALL

        SELECT
            t.id, t.name, t.accession, t.sequence_sha256,
            r.id, r.spec_sha256, r.status,
            NULL::uuid, NULL::text, NULL::varchar(64), NULL::integer,
            'tool_call'::text,
            tc.id,
            tc.tool_name,
            tc.status,
            tc.id,
            NULL::uuid,
            NULL::uuid,
            NULL::double precision,
            NULL::text,
            NULL::varchar(64),
            COALESCE(tc.output_sha256, tc.input_sha256),
            jsonb_build_object(
                'tool_version', tc.tool_version,
                'model_uri', tc.model_uri,
                'weights_sha256', tc.weights_sha256,
                'environment_sha256', tc.environment_sha256,
                'input_sha256', tc.input_sha256,
                'parameters', tc.parameters_json,
                'random_seed', tc.random_seed,
                'attempt', tc.attempt,
                'error', tc.error_json
            ),
            COALESCE(tc.finished_at, tc.started_at, tc.queued_at)
        FROM tool_calls tc
        JOIN experiment_runs r ON r.id = tc.run_id
        JOIN targets t ON t.id = r.target_id

        UNION ALL

        SELECT
            t.id, t.name, t.accession, t.sequence_sha256,
            r.id, r.spec_sha256, r.status,
            NULL::uuid, NULL::text, NULL::varchar(64), d.generation,
            'agent_decision'::text,
            d.id,
            d.decision_type,
            d.status,
            NULL::uuid,
            d.id,
            NULL::uuid,
            NULL::double precision,
            NULL::text,
            NULL::varchar(64),
            d.response_sha256,
            jsonb_build_object(
                'agent_name', d.agent_name,
                'agent_version', d.agent_version,
                'model_name', d.model_name,
                'prompt_sha256', d.prompt_sha256,
                'response_sha256', d.response_sha256,
                'prompt_artifact_id', d.prompt_artifact_id,
                'response_artifact_id', d.response_artifact_id,
                'structured', d.structured_json
            ),
            d.created_at
        FROM agent_decisions d
        JOIN experiment_runs r ON r.id = d.run_id
        JOIN targets t ON t.id = r.target_id

        UNION ALL

        SELECT
            t.id, t.name, t.accession, t.sequence_sha256,
            r.id, r.spec_sha256, r.status,
            NULL::uuid, NULL::text, NULL::varchar(64), NULL::integer,
            'artifact'::text,
            a.id,
            ea.role,
            tc.status,
            tc.id,
            NULL::uuid,
            a.id,
            NULL::double precision,
            a.storage_uri,
            a.media_type,
            a.sha256,
            jsonb_build_object(
                'size_bytes', a.size_bytes,
                'metadata', a.metadata_json,
                'tool_name', tc.tool_name,
                'tool_version', tc.tool_version
            ),
            a.created_at
        FROM evidence_artifacts ea
        JOIN artifacts a ON a.id = ea.artifact_id
        JOIN tool_calls tc ON tc.id = ea.tool_call_id
        JOIN experiment_runs r ON r.id = tc.run_id
        JOIN targets t ON t.id = r.target_id

        UNION ALL

        SELECT
            t.id, t.name, t.accession, t.sequence_sha256,
            r.id, r.spec_sha256, r.status,
            NULL::uuid, NULL::text, NULL::varchar(64), NULL::integer,
            'lifecycle_run'::text,
            le.id,
            le.event_type,
            NULL::varchar(32),
            NULL::uuid,
            NULL::uuid,
            NULL::uuid,
            NULL::double precision,
            NULL::text,
            NULL::varchar(64),
            le.payload_sha256,
            jsonb_build_object('actor', le.actor, 'payload', le.payload_json),
            le.occurred_at
        FROM lifecycle_events le
        JOIN experiment_runs r
          ON le.aggregate_type = 'run' AND le.aggregate_id = r.id
        JOIN targets t ON t.id = r.target_id

        UNION ALL

        SELECT
            t.id, t.name, t.accession, t.sequence_sha256,
            r.id, r.spec_sha256, r.status,
            c.id, c.sequence, c.sequence_sha256, c.generation,
            'lifecycle_candidate'::text,
            le.id,
            le.event_type,
            c.status,
            NULL::uuid,
            NULL::uuid,
            NULL::uuid,
            NULL::double precision,
            NULL::text,
            NULL::varchar(64),
            le.payload_sha256,
            jsonb_build_object('actor', le.actor, 'payload', le.payload_json),
            le.occurred_at
        FROM lifecycle_events le
        JOIN candidates c
          ON le.aggregate_type = 'candidate' AND le.aggregate_id = c.id
        JOIN experiment_runs r ON r.id = c.run_id
        JOIN targets t ON t.id = r.target_id

        UNION ALL

        SELECT
            t.id, t.name, t.accession, t.sequence_sha256,
            NULL::uuid, NULL::varchar(64), NULL::varchar(32),
            NULL::uuid, NULL::text, NULL::varchar(64), NULL::integer,
            'lifecycle_pocket'::text,
            le.id,
            le.event_type,
            p.status,
            NULL::uuid,
            NULL::uuid,
            NULL::uuid,
            NULL::double precision,
            NULL::text,
            NULL::varchar(64),
            le.payload_sha256,
            jsonb_build_object(
                'actor', le.actor,
                'payload', le.payload_json,
                'pocket_id', p.id,
                'pocket_key', p.pocket_key
            ),
            le.occurred_at
        FROM lifecycle_events le
        JOIN target_pockets p
          ON le.aggregate_type = 'pocket' AND le.aggregate_id = p.id
        JOIN targets t ON t.id = p.target_id
        """
    )
    op.execute(
        """
        CREATE VIEW research_experience_edges_v1 AS
        SELECT
            r.target_id,
            child.run_id,
            'tool_dependency'::text AS edge_kind,
            dep.parent_tool_call_id AS source_id,
            dep.child_tool_call_id AS destination_id,
            dep.relation_type,
            NULL::varchar(16) AS direction,
            dep.created_at
        FROM tool_call_dependencies dep
        JOIN tool_calls child ON child.id = dep.child_tool_call_id
        JOIN experiment_runs r ON r.id = child.run_id

        UNION ALL

        SELECT
            r.target_id,
            d.run_id,
            'agent_decision_tool_call'::text,
            edge.decision_id,
            edge.tool_call_id,
            edge.relation_type,
            edge.direction,
            edge.created_at
        FROM agent_decision_tool_call_edges edge
        JOIN agent_decisions d ON d.id = edge.decision_id
        JOIN experiment_runs r ON r.id = d.run_id

        UNION ALL

        SELECT
            r.target_id,
            tc.run_id,
            'tool_artifact'::text,
            ea.tool_call_id,
            ea.artifact_id,
            ea.role,
            'output'::varchar(16),
            a.created_at
        FROM evidence_artifacts ea
        JOIN tool_calls tc ON tc.id = ea.tool_call_id
        JOIN experiment_runs r ON r.id = tc.run_id
        JOIN artifacts a ON a.id = ea.artifact_id

        UNION ALL

        SELECT
            r.target_id,
            c.run_id,
            'candidate_parent'::text,
            c.parent_id,
            c.id,
            'derived_from'::varchar(64),
            'input'::varchar(16),
            c.created_at
        FROM candidates c
        JOIN experiment_runs r ON r.id = c.run_id
        WHERE c.parent_id IS NOT NULL

        UNION ALL

        SELECT
            r.target_id,
            c.run_id,
            'candidate_generator'::text,
            c.generator_call_id,
            c.id,
            'generated'::varchar(64),
            'output'::varchar(16),
            c.created_at
        FROM candidates c
        JOIN experiment_runs r ON r.id = c.run_id
        WHERE c.generator_call_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW research_experience_edges_v1")
    op.execute("DROP VIEW research_experience_records_v1")
