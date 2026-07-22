CREATE TABLE change_closure_results (
    closure_result_id text PRIMARY KEY,
    orchestration_id text NOT NULL,
    change_request_id text NOT NULL,
    project_id text NOT NULL,
    analysis_case_id text NOT NULL,
    edit_result_id text,
    test_data_execution_result_id text,
    ui_verification_result_id text,
    coverage_report_id text NOT NULL,
    component_digest text NOT NULL,
    status text NOT NULL,
    unresolved_items jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT change_closure_results_fields_not_blank CHECK (
        btrim(closure_result_id) <> ''
        AND btrim(orchestration_id) <> ''
        AND btrim(created_by) <> ''
    ),
    CONSTRAINT change_closure_results_digest_sha256 CHECK (
        component_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT change_closure_results_status_valid CHECK (
        status IN ('passed', 'failed', 'blocked', 'reanalysis_required')
    ),
    CONSTRAINT change_closure_results_unresolved_valid CHECK (
        jsonb_typeof(unresolved_items) = 'array'
        AND (
            (status = 'passed' AND jsonb_array_length(unresolved_items) = 0)
            OR (status <> 'passed' AND jsonb_array_length(unresolved_items) > 0)
        )
    ),
    CONSTRAINT change_closure_results_artifact_fk FOREIGN KEY (closure_result_id)
        REFERENCES artifact_records(artifact_id),
    CONSTRAINT change_closure_results_orchestration_fk FOREIGN KEY (
        orchestration_id, project_id
    ) REFERENCES change_orchestrations(orchestration_id, project_id),
    CONSTRAINT change_closure_results_request_fk FOREIGN KEY (
        change_request_id, project_id
    ) REFERENCES change_requests(change_request_id, project_id),
    CONSTRAINT change_closure_results_case_fk FOREIGN KEY (
        analysis_case_id, project_id
    ) REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT change_closure_results_edit_fk FOREIGN KEY (
        edit_result_id, project_id
    ) REFERENCES edit_results(edit_result_id, project_id),
    CONSTRAINT change_closure_results_test_data_fk FOREIGN KEY (
        test_data_execution_result_id, project_id
    ) REFERENCES test_data_execution_runs(execution_result_id, project_id),
    CONSTRAINT change_closure_results_ui_fk FOREIGN KEY (
        ui_verification_result_id, project_id
    ) REFERENCES change_validations(verification_result_id, project_id),
    CONSTRAINT change_closure_results_coverage_fk FOREIGN KEY (coverage_report_id)
        REFERENCES artifact_records(artifact_id),
    CONSTRAINT change_closure_results_scope_identity_unique UNIQUE (
        closure_result_id, project_id
    ),
    CONSTRAINT change_closure_results_basis_unique UNIQUE (
        orchestration_id, component_digest
    )
);

CREATE INDEX change_closure_results_request_idx
    ON change_closure_results (
        project_id, change_request_id, created_at DESC, closure_result_id DESC
    );
