CREATE TABLE verification_scenarios (
    scenario_version_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    scenario_id text NOT NULL,
    scenario_version text NOT NULL,
    title text NOT NULL,
    preconditions jsonb NOT NULL,
    steps jsonb NOT NULL,
    expected_visible_results jsonb NOT NULL,
    evidence_requirements jsonb NOT NULL,
    trigger_path text NOT NULL,
    data_recipe_ref text,
    review_status text NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT verification_scenarios_fields_not_blank CHECK (
        btrim(scenario_version_id) <> ''
        AND btrim(scenario_id) <> ''
        AND btrim(scenario_version) <> ''
        AND btrim(title) <> ''
        AND btrim(trigger_path) <> ''
    ),
    CONSTRAINT verification_scenarios_arrays_valid CHECK (
        jsonb_typeof(preconditions) = 'array'
        AND jsonb_typeof(steps) = 'array'
        AND jsonb_array_length(steps) > 0
        AND jsonb_typeof(expected_visible_results) = 'array'
        AND jsonb_array_length(expected_visible_results) > 0
        AND jsonb_typeof(evidence_requirements) = 'array'
        AND jsonb_array_length(evidence_requirements) > 0
    ),
    CONSTRAINT verification_scenarios_review_valid CHECK (
        review_status IN ('draft', 'approved', 'rejected')
    ),
    CONSTRAINT verification_scenarios_active_consistent CHECK (
        NOT is_active OR review_status = 'approved'
    ),
    CONSTRAINT verification_scenarios_semantic_unique UNIQUE (
        project_id, scenario_id, scenario_version
    ),
    CONSTRAINT verification_scenarios_scope_identity_unique UNIQUE (
        scenario_version_id, project_id
    )
);

CREATE UNIQUE INDEX verification_scenarios_active_unique
    ON verification_scenarios (project_id, scenario_id)
    WHERE is_active;

CREATE TABLE ui_environments (
    environment_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    base_url text NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_environments_fields_not_blank CHECK (
        btrim(environment_id) <> '' AND btrim(base_url) <> ''
    ),
    CONSTRAINT ui_environments_status_valid CHECK (status IN ('active', 'inactive')),
    CONSTRAINT ui_environments_scope_identity_unique UNIQUE (environment_id, project_id)
);

CREATE TABLE ui_deployments (
    deployment_revision text NOT NULL,
    environment_id text NOT NULL,
    project_id text NOT NULL,
    repository_revision text NOT NULL,
    status text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_deployments_fields_not_blank CHECK (
        btrim(deployment_revision) <> '' AND btrim(repository_revision) <> ''
    ),
    CONSTRAINT ui_deployments_status_valid CHECK (status IN ('ready', 'failed', 'stale')),
    CONSTRAINT ui_deployments_environment_fk FOREIGN KEY (environment_id, project_id)
        REFERENCES ui_environments(environment_id, project_id),
    CONSTRAINT ui_deployments_identity PRIMARY KEY (environment_id, deployment_revision),
    CONSTRAINT ui_deployments_scope_identity_unique UNIQUE (
        deployment_revision, environment_id, project_id
    )
);

CREATE TABLE ui_execution_plans (
    ui_execution_plan_id text PRIMARY KEY,
    project_id text NOT NULL,
    analysis_case_id text NOT NULL,
    edit_packet_id text NOT NULL,
    edit_result_id text NOT NULL,
    environment_id text NOT NULL,
    deployment_revision text NOT NULL,
    repository_revision text NOT NULL,
    status text NOT NULL,
    scenario_refs jsonb NOT NULL,
    blocking_reasons jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_execution_plans_fields_not_blank CHECK (
        btrim(ui_execution_plan_id) <> '' AND btrim(repository_revision) <> ''
    ),
    CONSTRAINT ui_execution_plans_status_valid CHECK (
        status IN ('preflight_pending', 'ready', 'blocked', 'completed')
    ),
    CONSTRAINT ui_execution_plans_arrays_valid CHECK (
        jsonb_typeof(scenario_refs) = 'array'
        AND jsonb_array_length(scenario_refs) > 0
        AND jsonb_typeof(blocking_reasons) = 'array'
    ),
    CONSTRAINT ui_execution_plans_blocked_consistent CHECK (
        (status = 'blocked' AND jsonb_array_length(blocking_reasons) > 0)
        OR (status <> 'blocked' AND jsonb_array_length(blocking_reasons) = 0)
    ),
    CONSTRAINT ui_execution_plans_case_fk FOREIGN KEY (analysis_case_id, project_id)
        REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT ui_execution_plans_packet_fk FOREIGN KEY (edit_packet_id, project_id)
        REFERENCES edit_packets(edit_packet_id, project_id),
    CONSTRAINT ui_execution_plans_result_fk FOREIGN KEY (edit_result_id, project_id)
        REFERENCES edit_results(edit_result_id, project_id),
    CONSTRAINT ui_execution_plans_environment_fk FOREIGN KEY (environment_id, project_id)
        REFERENCES ui_environments(environment_id, project_id),
    CONSTRAINT ui_execution_plans_deployment_fk FOREIGN KEY (
        deployment_revision, environment_id, project_id
    ) REFERENCES ui_deployments(deployment_revision, environment_id, project_id),
    CONSTRAINT ui_execution_plans_scope_identity_unique UNIQUE (
        ui_execution_plan_id, project_id
    )
);

CREATE TABLE ui_execution_plan_scenarios (
    ui_execution_plan_id text NOT NULL,
    project_id text NOT NULL,
    scenario_id text NOT NULL,
    scenario_version_id text NOT NULL,
    execution_order integer NOT NULL,
    CONSTRAINT ui_execution_plan_scenarios_order_valid CHECK (execution_order >= 1),
    CONSTRAINT ui_execution_plan_scenarios_plan_fk FOREIGN KEY (
        ui_execution_plan_id, project_id
    ) REFERENCES ui_execution_plans(ui_execution_plan_id, project_id),
    CONSTRAINT ui_execution_plan_scenarios_version_fk FOREIGN KEY (
        scenario_version_id, project_id
    ) REFERENCES verification_scenarios(scenario_version_id, project_id),
    CONSTRAINT ui_execution_plan_scenarios_identity PRIMARY KEY (
        ui_execution_plan_id, scenario_id
    ),
    CONSTRAINT ui_execution_plan_scenarios_order_unique UNIQUE (
        ui_execution_plan_id, execution_order
    )
);

CREATE TABLE ui_preflight_checks (
    ui_preflight_check_id text PRIMARY KEY,
    ui_execution_plan_id text NOT NULL,
    project_id text NOT NULL,
    check_type text NOT NULL,
    status text NOT NULL,
    evidence_ref text,
    reason text,
    checked_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_preflight_checks_type_valid CHECK (
        check_type IN ('environment', 'authentication', 'test_data', 'trigger_path', 'locator')
    ),
    CONSTRAINT ui_preflight_checks_status_valid CHECK (status IN ('passed', 'failed', 'blocked')),
    CONSTRAINT ui_preflight_checks_reason_consistent CHECK (
        status = 'passed' OR (reason IS NOT NULL AND btrim(reason) <> '')
    ),
    CONSTRAINT ui_preflight_checks_plan_fk FOREIGN KEY (
        ui_execution_plan_id, project_id
    ) REFERENCES ui_execution_plans(ui_execution_plan_id, project_id),
    CONSTRAINT ui_preflight_checks_type_unique UNIQUE (
        ui_execution_plan_id, check_type
    )
);

CREATE TABLE ui_execution_runs (
    ui_execution_run_id text PRIMARY KEY,
    ui_execution_plan_id text NOT NULL,
    project_id text NOT NULL,
    status text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CONSTRAINT ui_execution_runs_status_valid CHECK (
        status IN ('running', 'completed', 'failed', 'blocked')
    ),
    CONSTRAINT ui_execution_runs_completion_consistent CHECK (
        (status = 'running' AND completed_at IS NULL)
        OR (status <> 'running' AND completed_at IS NOT NULL)
    ),
    CONSTRAINT ui_execution_runs_plan_fk FOREIGN KEY (
        ui_execution_plan_id, project_id
    ) REFERENCES ui_execution_plans(ui_execution_plan_id, project_id),
    CONSTRAINT ui_execution_runs_scope_identity_unique UNIQUE (
        ui_execution_run_id, project_id
    )
);

CREATE TABLE ui_execution_evidence (
    evidence_id text PRIMARY KEY,
    ui_execution_run_id text NOT NULL,
    project_id text NOT NULL,
    scenario_id text NOT NULL,
    evidence_type text NOT NULL,
    evidence_ref text NOT NULL,
    content_digest text NOT NULL,
    sanitized boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_execution_evidence_fields_not_blank CHECK (
        btrim(evidence_id) <> '' AND btrim(evidence_ref) <> ''
    ),
    CONSTRAINT ui_execution_evidence_type_valid CHECK (
        evidence_type IN ('screenshot', 'assertion', 'network_summary', 'step_log')
    ),
    CONSTRAINT ui_execution_evidence_digest_sha256 CHECK (
        content_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ui_execution_evidence_must_be_sanitized CHECK (sanitized),
    CONSTRAINT ui_execution_evidence_run_fk FOREIGN KEY (
        ui_execution_run_id, project_id
    ) REFERENCES ui_execution_runs(ui_execution_run_id, project_id)
);

CREATE TABLE ui_scenario_results (
    ui_execution_run_id text NOT NULL,
    project_id text NOT NULL,
    scenario_id text NOT NULL,
    status text NOT NULL,
    impact_item_refs jsonb NOT NULL,
    evidence_refs jsonb NOT NULL,
    failure_category text NOT NULL,
    summary text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_scenario_results_status_valid CHECK (
        status IN ('passed', 'failed', 'blocked', 'skipped')
    ),
    CONSTRAINT ui_scenario_results_failure_category_valid CHECK (
        failure_category IN (
            'none',
            'business_assertion',
            'environment',
            'test_data',
            'locator',
            'authentication',
            'blocked'
        )
    ),
    CONSTRAINT ui_scenario_results_arrays_valid CHECK (
        jsonb_typeof(impact_item_refs) = 'array'
        AND jsonb_typeof(evidence_refs) = 'array'
    ),
    CONSTRAINT ui_scenario_results_status_consistent CHECK (
        (status = 'passed' AND failure_category = 'none')
        OR (status <> 'passed' AND failure_category <> 'none')
    ),
    CONSTRAINT ui_scenario_results_run_fk FOREIGN KEY (
        ui_execution_run_id, project_id
    ) REFERENCES ui_execution_runs(ui_execution_run_id, project_id),
    CONSTRAINT ui_scenario_results_identity PRIMARY KEY (
        ui_execution_run_id, scenario_id
    )
);

CREATE TABLE change_validations (
    verification_result_id text PRIMARY KEY,
    project_id text NOT NULL,
    analysis_case_id text NOT NULL,
    ui_execution_plan_id text NOT NULL,
    ui_execution_run_id text,
    status text NOT NULL,
    unresolved_impact_item_ids jsonb NOT NULL,
    out_of_scope_files jsonb NOT NULL,
    failure_reasons jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT change_validations_status_valid CHECK (
        status IN ('passed', 'failed', 'blocked', 'reanalysis_required')
    ),
    CONSTRAINT change_validations_arrays_valid CHECK (
        jsonb_typeof(unresolved_impact_item_ids) = 'array'
        AND jsonb_typeof(out_of_scope_files) = 'array'
        AND jsonb_typeof(failure_reasons) = 'array'
    ),
    CONSTRAINT change_validations_case_fk FOREIGN KEY (analysis_case_id, project_id)
        REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT change_validations_artifact_fk FOREIGN KEY (verification_result_id)
        REFERENCES artifact_records(artifact_id),
    CONSTRAINT change_validations_plan_fk FOREIGN KEY (
        ui_execution_plan_id, project_id
    ) REFERENCES ui_execution_plans(ui_execution_plan_id, project_id),
    CONSTRAINT change_validations_run_fk FOREIGN KEY (
        ui_execution_run_id, project_id
    ) REFERENCES ui_execution_runs(ui_execution_run_id, project_id),
    CONSTRAINT change_validations_scope_identity_unique UNIQUE (
        verification_result_id, project_id
    )
);

CREATE INDEX ui_execution_plans_case_idx
    ON ui_execution_plans (project_id, analysis_case_id, created_at DESC);
CREATE INDEX ui_execution_evidence_run_scenario_idx
    ON ui_execution_evidence (project_id, ui_execution_run_id, scenario_id);
