ALTER TABLE test_data_execution_runs
    ADD COLUMN test_data_token text,
    ADD COLUMN runtime_variables jsonb,
    ADD CONSTRAINT test_data_execution_runs_token_format CHECK (
        test_data_token IS NULL
        OR test_data_token ~ '^OM-E2E-[0-9]{8}-[0-9A-F]{8}$'
    ),
    ADD CONSTRAINT test_data_execution_runs_runtime_variables_object CHECK (
        runtime_variables IS NULL OR jsonb_typeof(runtime_variables) = 'object'
    ),
    ADD CONSTRAINT test_data_execution_runs_token_unique UNIQUE (test_data_token);

ALTER TABLE test_data_flow_results
    ADD COLUMN test_data_binding_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD CONSTRAINT test_data_flow_results_binding_refs_array CHECK (
        jsonb_typeof(test_data_binding_refs) = 'array'
    );

ALTER TABLE test_data_step_results
    ADD COLUMN test_data_binding_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD CONSTRAINT test_data_step_results_binding_refs_array CHECK (
        jsonb_typeof(test_data_binding_refs) = 'array'
    );

ALTER TABLE test_data_identity_bindings
    ADD CONSTRAINT test_data_identity_bindings_full_scope_unique UNIQUE (
        binding_id, run_id, project_id
    );

ALTER TABLE test_data_execution_evidence
    ADD COLUMN test_data_binding_ref text,
    ADD CONSTRAINT test_data_execution_evidence_binding_fk FOREIGN KEY (
        test_data_binding_ref, run_id, project_id
    ) REFERENCES test_data_identity_bindings (
        binding_id, run_id, project_id
    ) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE test_data_run_contexts (
    run_id text PRIMARY KEY,
    project_id text NOT NULL,
    runtime_variables jsonb NOT NULL,
    flow_dependencies jsonb NOT NULL,
    evidence_refs jsonb NOT NULL,
    content_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT test_data_run_contexts_objects_valid CHECK (
        jsonb_typeof(runtime_variables) = 'object'
        AND jsonb_typeof(flow_dependencies) = 'object'
        AND jsonb_typeof(evidence_refs) = 'array'
    ),
    CONSTRAINT test_data_run_contexts_digest_sha256 CHECK (
        content_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT test_data_run_contexts_run_fk FOREIGN KEY (run_id, project_id)
        REFERENCES test_data_execution_runs(run_id, project_id)
);

CREATE TABLE project_data_identity_profiles (
    project_id text NOT NULL REFERENCES projects(project_id),
    provider_ref text NOT NULL,
    provider_type text NOT NULL,
    lookup_steps jsonb NOT NULL,
    cleanup_steps jsonb NOT NULL DEFAULT '[]'::jsonb,
    identity_definition jsonb NOT NULL,
    business_summary_fields jsonb NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    active boolean NOT NULL DEFAULT true,
    reviewed_by text NOT NULL,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT project_data_identity_profiles_identity PRIMARY KEY (
        project_id, provider_ref
    ),
    CONSTRAINT project_data_identity_profiles_fields_not_blank CHECK (
        btrim(provider_ref) <> '' AND btrim(reviewed_by) <> ''
    ),
    CONSTRAINT project_data_identity_profiles_provider_valid CHECK (
        provider_type IN ('database', 'api', 'ui', 'hybrid')
    ),
    CONSTRAINT project_data_identity_profiles_json_valid CHECK (
        jsonb_typeof(lookup_steps) = 'array'
        AND jsonb_array_length(lookup_steps) > 0
        AND jsonb_typeof(cleanup_steps) = 'array'
        AND jsonb_typeof(identity_definition) = 'object'
        AND jsonb_typeof(business_summary_fields) = 'array'
        AND jsonb_array_length(business_summary_fields) > 0
    ),
    CONSTRAINT project_data_identity_profiles_revision_positive CHECK (revision >= 1)
);

CREATE TABLE existing_test_data_registrations (
    registration_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    data_name text NOT NULL,
    business_unique_value text NOT NULL,
    test_case_ref text NOT NULL,
    retain_after_test boolean NOT NULL,
    status text NOT NULL,
    provider_ref text,
    provider_type text,
    match_count integer,
    business_summary jsonb,
    identity_candidate jsonb,
    evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    plan_data_definition jsonb,
    blocking_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    requested_by text NOT NULL,
    requested_at timestamptz NOT NULL,
    confirmed_by text,
    confirmed_at timestamptz,
    CONSTRAINT existing_test_data_registrations_fields_not_blank CHECK (
        btrim(registration_id) <> ''
        AND btrim(data_name) <> ''
        AND btrim(business_unique_value) <> ''
        AND btrim(test_case_ref) <> ''
        AND btrim(requested_by) <> ''
    ),
    CONSTRAINT existing_test_data_registrations_status_valid CHECK (
        status IN ('candidate', 'confirmed', 'blocked')
    ),
    CONSTRAINT existing_test_data_registrations_json_valid CHECK (
        (business_summary IS NULL OR jsonb_typeof(business_summary) = 'object')
        AND (identity_candidate IS NULL OR jsonb_typeof(identity_candidate) = 'object')
        AND jsonb_typeof(evidence_refs) = 'array'
        AND (plan_data_definition IS NULL OR jsonb_typeof(plan_data_definition) = 'object')
        AND jsonb_typeof(blocking_reasons) = 'array'
    ),
    CONSTRAINT existing_test_data_registrations_state_consistent CHECK (
        (
            status = 'candidate'
            AND provider_ref IS NOT NULL
            AND provider_type IN ('database', 'api', 'ui', 'hybrid')
            AND match_count = 1
            AND business_summary IS NOT NULL
            AND identity_candidate IS NOT NULL
            AND jsonb_array_length(evidence_refs) > 0
            AND plan_data_definition IS NULL
            AND jsonb_array_length(blocking_reasons) = 0
            AND confirmed_by IS NULL
            AND confirmed_at IS NULL
        )
        OR (
            status = 'confirmed'
            AND match_count = 1
            AND plan_data_definition IS NOT NULL
            AND confirmed_by IS NOT NULL
            AND confirmed_at IS NOT NULL
            AND jsonb_array_length(blocking_reasons) = 0
        )
        OR (
            status = 'blocked'
            AND jsonb_array_length(blocking_reasons) > 0
            AND plan_data_definition IS NULL
            AND confirmed_by IS NULL
            AND confirmed_at IS NULL
        )
    ),
    CONSTRAINT existing_test_data_registrations_provider_fk FOREIGN KEY (
        project_id, provider_ref
    ) REFERENCES project_data_identity_profiles(project_id, provider_ref)
);

CREATE INDEX existing_test_data_registrations_project_idx
    ON existing_test_data_registrations (
        project_id, requested_at DESC, registration_id DESC
    );

COMMENT ON TABLE existing_test_data_registrations IS
    'Human-confirmed adoption candidates; stores only sanitized business values and Evidence refs';
