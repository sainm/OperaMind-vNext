CREATE TABLE change_orchestrations (
    orchestration_id text PRIMARY KEY,
    change_request_id text NOT NULL,
    project_id text NOT NULL,
    analysis_case_id text NOT NULL,
    impact_report_id text NOT NULL,
    reviewed_case_id text NOT NULL,
    reviewed_case_digest text NOT NULL,
    status text NOT NULL,
    acceptance_criteria_id text NOT NULL,
    test_plan_id text NOT NULL,
    test_data_plan_id text NOT NULL,
    coverage_report_id text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT change_orchestrations_fields_not_blank CHECK (
        btrim(orchestration_id) <> ''
        AND btrim(reviewed_case_id) <> ''
        AND btrim(created_by) <> ''
    ),
    CONSTRAINT change_orchestrations_digest_sha256 CHECK (
        reviewed_case_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT change_orchestrations_status_valid CHECK (
        status IN ('ready', 'blocked')
    ),
    CONSTRAINT change_orchestrations_artifact_fk FOREIGN KEY (orchestration_id)
        REFERENCES artifact_records(artifact_id),
    CONSTRAINT change_orchestrations_request_fk FOREIGN KEY (
        change_request_id, project_id
    ) REFERENCES change_requests(change_request_id, project_id),
    CONSTRAINT change_orchestrations_case_fk FOREIGN KEY (
        analysis_case_id, project_id
    ) REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT change_orchestrations_impact_fk FOREIGN KEY (
        impact_report_id, project_id
    ) REFERENCES impact_reports(impact_report_id, project_id),
    CONSTRAINT change_orchestrations_acceptance_fk FOREIGN KEY (acceptance_criteria_id)
        REFERENCES artifact_records(artifact_id),
    CONSTRAINT change_orchestrations_test_plan_fk FOREIGN KEY (test_plan_id)
        REFERENCES artifact_records(artifact_id),
    CONSTRAINT change_orchestrations_test_data_fk FOREIGN KEY (test_data_plan_id)
        REFERENCES artifact_records(artifact_id),
    CONSTRAINT change_orchestrations_coverage_fk FOREIGN KEY (coverage_report_id)
        REFERENCES artifact_records(artifact_id),
    CONSTRAINT change_orchestrations_scope_identity_unique UNIQUE (
        orchestration_id, project_id
    ),
    CONSTRAINT change_orchestrations_basis_unique UNIQUE (
        change_request_id, analysis_case_id, impact_report_id, reviewed_case_digest
    )
);

CREATE INDEX change_orchestrations_request_idx
    ON change_orchestrations (
        project_id, change_request_id, created_at DESC, orchestration_id DESC
    );
