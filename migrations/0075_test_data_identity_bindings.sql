ALTER TABLE test_data_execution_evidence
    DROP CONSTRAINT test_data_execution_evidence_type_valid,
    ADD CONSTRAINT test_data_execution_evidence_type_valid CHECK (
        evidence_type IN (
            'fixture', 'request', 'response', 'sql', 'screenshot',
            'assertion', 'step_log', 'cleanup', 'data_binding'
        )
    );

ALTER TABLE project_target_data_query_bindings
    ADD COLUMN identity_contract jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD CONSTRAINT project_target_data_query_bindings_identity_contract_object CHECK (
        jsonb_typeof(identity_contract) = 'object'
    );

CREATE TABLE test_data_identity_bindings (
    binding_id text PRIMARY KEY,
    run_id text NOT NULL,
    project_id text NOT NULL,
    test_data_id text NOT NULL,
    binding_mode text NOT NULL,
    source_flow_id text NOT NULL,
    source_phase text NOT NULL DEFAULT 'setup',
    source_step_id text NOT NULL,
    primary_key jsonb NOT NULL,
    business_unique_keys jsonb NOT NULL,
    screen_key jsonb NOT NULL,
    screen_locator jsonb NOT NULL,
    match_count integer NOT NULL,
    frozen_at timestamptz NOT NULL,
    content_digest text NOT NULL,
    evidence_ref text NOT NULL,
    CONSTRAINT test_data_identity_bindings_fields_not_blank CHECK (
        btrim(binding_id) <> '' AND btrim(test_data_id) <> ''
        AND btrim(source_flow_id) <> '' AND btrim(source_step_id) <> ''
        AND btrim(evidence_ref) <> ''
    ),
    CONSTRAINT test_data_identity_bindings_mode_valid CHECK (
        binding_mode IN ('generated', 'adopted')
    ),
    CONSTRAINT test_data_identity_bindings_source_phase_valid CHECK (
        source_phase = 'setup'
    ),
    CONSTRAINT test_data_identity_bindings_values_valid CHECK (
        jsonb_typeof(primary_key) = 'object'
        AND jsonb_typeof(business_unique_keys) = 'array'
        AND jsonb_array_length(business_unique_keys) > 0
        AND jsonb_typeof(screen_key) = 'object'
        AND jsonb_typeof(screen_locator) = 'object'
        AND match_count = 1
    ),
    CONSTRAINT test_data_identity_bindings_digest_sha256 CHECK (
        content_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT test_data_identity_bindings_run_fk FOREIGN KEY (run_id, project_id)
        REFERENCES test_data_execution_runs(run_id, project_id),
    CONSTRAINT test_data_identity_bindings_source_step_fk FOREIGN KEY (
        run_id, project_id, source_flow_id, source_phase, source_step_id
    ) REFERENCES test_data_step_results (
        run_id, project_id, flow_id, phase, step_id
    ),
    CONSTRAINT test_data_identity_bindings_run_data_unique UNIQUE (
        run_id, test_data_id
    ),
    CONSTRAINT test_data_identity_bindings_evidence_ref_unique UNIQUE (evidence_ref)
);

CREATE INDEX test_data_identity_bindings_run_idx
    ON test_data_identity_bindings (project_id, run_id, test_data_id);
