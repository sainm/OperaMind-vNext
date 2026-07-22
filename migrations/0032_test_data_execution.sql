ALTER TABLE change_orchestrations
    ADD CONSTRAINT change_orchestrations_test_data_scope_unique UNIQUE (
        orchestration_id, test_data_plan_id, project_id
    );

CREATE TABLE test_data_execution_runs (
    run_id text PRIMARY KEY,
    execution_result_id text NOT NULL,
    orchestration_id text NOT NULL,
    test_data_plan_id text NOT NULL,
    approval_grant_id text NOT NULL,
    project_id text NOT NULL,
    analysis_case_id text NOT NULL,
    status text NOT NULL,
    result_artifact_id text,
    created_by text NOT NULL,
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    CONSTRAINT test_data_execution_runs_fields_not_blank CHECK (
        btrim(run_id) <> ''
        AND btrim(execution_result_id) <> ''
        AND btrim(created_by) <> ''
    ),
    CONSTRAINT test_data_execution_runs_status_valid CHECK (
        status IN ('running', 'passed', 'failed', 'blocked', 'interrupted')
    ),
    CONSTRAINT test_data_execution_runs_completion_consistent CHECK (
        (
            status = 'running'
            AND completed_at IS NULL
            AND result_artifact_id IS NULL
        )
        OR (
            status <> 'running'
            AND completed_at IS NOT NULL
            AND result_artifact_id = execution_result_id
        )
    ),
    CONSTRAINT test_data_execution_runs_time_order CHECK (
        completed_at IS NULL OR completed_at >= started_at
    ),
    CONSTRAINT test_data_execution_runs_orchestration_fk FOREIGN KEY (
        orchestration_id, test_data_plan_id, project_id
    ) REFERENCES change_orchestrations (
        orchestration_id, test_data_plan_id, project_id
    ),
    CONSTRAINT test_data_execution_runs_grant_fk FOREIGN KEY (
        approval_grant_id, project_id
    ) REFERENCES approval_grants(approval_grant_id, project_id),
    CONSTRAINT test_data_execution_runs_case_fk FOREIGN KEY (
        analysis_case_id, project_id
    ) REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT test_data_execution_runs_result_fk FOREIGN KEY (result_artifact_id)
        REFERENCES artifact_records(artifact_id),
    CONSTRAINT test_data_execution_runs_scope_identity_unique UNIQUE (
        run_id, project_id
    ),
    CONSTRAINT test_data_execution_runs_result_unique UNIQUE (
        execution_result_id, project_id
    )
);

CREATE TABLE test_data_flow_results (
    run_id text NOT NULL,
    project_id text NOT NULL,
    flow_id text NOT NULL,
    execution_order integer NOT NULL,
    status text NOT NULL,
    deferred_assertion_ids jsonb NOT NULL,
    CONSTRAINT test_data_flow_results_order_valid CHECK (execution_order >= 1),
    CONSTRAINT test_data_flow_results_status_valid CHECK (
        status IN ('passed', 'failed', 'blocked', 'not_run')
    ),
    CONSTRAINT test_data_flow_results_deferred_array CHECK (
        jsonb_typeof(deferred_assertion_ids) = 'array'
    ),
    CONSTRAINT test_data_flow_results_run_fk FOREIGN KEY (run_id, project_id)
        REFERENCES test_data_execution_runs(run_id, project_id),
    CONSTRAINT test_data_flow_results_identity PRIMARY KEY (run_id, flow_id),
    CONSTRAINT test_data_flow_results_order_unique UNIQUE (run_id, execution_order),
    CONSTRAINT test_data_flow_results_scope_identity_unique UNIQUE (
        run_id, project_id, flow_id
    )
);

CREATE TABLE test_data_step_results (
    run_id text NOT NULL,
    project_id text NOT NULL,
    flow_id text NOT NULL,
    phase text NOT NULL,
    step_id text NOT NULL,
    sequence integer NOT NULL,
    channel text NOT NULL,
    status text NOT NULL,
    output_variables jsonb NOT NULL,
    evidence_refs jsonb NOT NULL,
    failure_reason text,
    CONSTRAINT test_data_step_results_sequence_valid CHECK (sequence >= 1),
    CONSTRAINT test_data_step_results_phase_valid CHECK (
        phase IN ('setup', 'cleanup')
    ),
    CONSTRAINT test_data_step_results_channel_valid CHECK (
        channel IN ('fixture', 'http', 'sql', 'ui')
    ),
    CONSTRAINT test_data_step_results_status_valid CHECK (
        status IN ('passed', 'failed', 'blocked', 'not_run')
    ),
    CONSTRAINT test_data_step_results_arrays_valid CHECK (
        jsonb_typeof(output_variables) = 'array'
        AND jsonb_typeof(evidence_refs) = 'array'
    ),
    CONSTRAINT test_data_step_results_failure_consistent CHECK (
        (
            status IN ('failed', 'blocked')
            AND failure_reason IS NOT NULL
            AND btrim(failure_reason) <> ''
        )
        OR (status IN ('passed', 'not_run') AND failure_reason IS NULL)
    ),
    CONSTRAINT test_data_step_results_flow_fk FOREIGN KEY (
        run_id, project_id, flow_id
    ) REFERENCES test_data_flow_results(run_id, project_id, flow_id),
    CONSTRAINT test_data_step_results_identity PRIMARY KEY (
        run_id, flow_id, phase, step_id
    ),
    CONSTRAINT test_data_step_results_order_unique UNIQUE (
        run_id, flow_id, phase, sequence
    ),
    CONSTRAINT test_data_step_results_scope_identity_unique UNIQUE (
        run_id, project_id, flow_id, phase, step_id
    )
);

CREATE TABLE test_data_execution_evidence (
    evidence_id text PRIMARY KEY,
    run_id text NOT NULL,
    project_id text NOT NULL,
    flow_id text NOT NULL,
    phase text NOT NULL,
    step_id text NOT NULL,
    evidence_type text NOT NULL,
    evidence_ref text NOT NULL,
    content_digest text NOT NULL,
    sanitized boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT test_data_execution_evidence_fields_not_blank CHECK (
        btrim(evidence_id) <> '' AND btrim(evidence_ref) <> ''
    ),
    CONSTRAINT test_data_execution_evidence_type_valid CHECK (
        evidence_type IN (
            'fixture', 'request', 'response', 'sql', 'screenshot',
            'assertion', 'step_log', 'cleanup'
        )
    ),
    CONSTRAINT test_data_execution_evidence_digest_sha256 CHECK (
        content_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT test_data_execution_evidence_must_be_sanitized CHECK (sanitized),
    CONSTRAINT test_data_execution_evidence_step_fk FOREIGN KEY (
        run_id, project_id, flow_id, phase, step_id
    ) REFERENCES test_data_step_results(
        run_id, project_id, flow_id, phase, step_id
    ),
    CONSTRAINT test_data_execution_evidence_ref_unique UNIQUE (evidence_ref),
    CONSTRAINT test_data_execution_evidence_scope_identity_unique UNIQUE (
        evidence_id, project_id
    )
);

CREATE INDEX test_data_execution_runs_orchestration_idx
    ON test_data_execution_runs (
        project_id, orchestration_id, started_at DESC, run_id DESC
    );

CREATE INDEX test_data_execution_evidence_run_idx
    ON test_data_execution_evidence (project_id, run_id, flow_id, phase, step_id);
