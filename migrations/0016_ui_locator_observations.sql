ALTER TABLE ui_locator_candidates
    DROP CONSTRAINT ui_locator_candidates_pkey,
    ADD CONSTRAINT ui_locator_candidates_identity PRIMARY KEY (
        ui_knowledge_snapshot_id, locator_candidate_id
    );

CREATE TABLE ui_locator_observation_runs (
    ui_locator_observation_run_id text PRIMARY KEY,
    project_id text NOT NULL,
    source_ui_knowledge_snapshot_id text NOT NULL,
    result_ui_knowledge_snapshot_id text,
    environment_id text NOT NULL,
    deployment_revision text NOT NULL,
    status text NOT NULL,
    issues jsonb NOT NULL,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_locator_observation_runs_fields_not_blank CHECK (
        btrim(ui_locator_observation_run_id) <> ''
    ),
    CONSTRAINT ui_locator_observation_runs_status_valid CHECK (
        status IN ('completed', 'partial', 'blocked')
        AND (
            (status = 'blocked' AND result_ui_knowledge_snapshot_id IS NULL)
            OR (status <> 'blocked' AND result_ui_knowledge_snapshot_id IS NOT NULL)
        )
    ),
    CONSTRAINT ui_locator_observation_runs_issues_valid CHECK (
        jsonb_typeof(issues) = 'array'
    ),
    CONSTRAINT ui_locator_observation_runs_digest_sha256 CHECK (
        payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ui_locator_observation_runs_source_fk FOREIGN KEY (
        source_ui_knowledge_snapshot_id, project_id
    ) REFERENCES ui_knowledge_snapshots(ui_knowledge_snapshot_id, project_id),
    CONSTRAINT ui_locator_observation_runs_result_fk FOREIGN KEY (
        result_ui_knowledge_snapshot_id, project_id
    ) REFERENCES ui_knowledge_snapshots(ui_knowledge_snapshot_id, project_id),
    CONSTRAINT ui_locator_observation_runs_deployment_fk FOREIGN KEY (
        deployment_revision, environment_id, project_id
    ) REFERENCES ui_deployments(deployment_revision, environment_id, project_id),
    CONSTRAINT ui_locator_observation_runs_scope_identity_unique UNIQUE (
        ui_locator_observation_run_id, project_id
    )
);

CREATE TABLE ui_locator_observations (
    ui_locator_observation_id text PRIMARY KEY,
    ui_locator_observation_run_id text NOT NULL,
    project_id text NOT NULL,
    source_ui_knowledge_snapshot_id text NOT NULL,
    target_ref text NOT NULL,
    locator_candidate_id text NOT NULL,
    strategy text NOT NULL,
    locator_value text NOT NULL,
    accessible_name text,
    exact_match boolean NOT NULL,
    status text NOT NULL,
    match_count integer NOT NULL,
    visible_count integer NOT NULL,
    discovered boolean NOT NULL,
    evidence_ref text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_locator_observations_fields_not_blank CHECK (
        btrim(ui_locator_observation_id) <> ''
        AND btrim(locator_candidate_id) <> ''
        AND btrim(locator_value) <> ''
        AND btrim(evidence_ref) <> ''
    ),
    CONSTRAINT ui_locator_observations_strategy_valid CHECK (
        strategy IN ('role', 'label', 'text', 'test_id', 'placeholder', 'css')
    ),
    CONSTRAINT ui_locator_observations_role_name_valid CHECK (
        (strategy = 'role' AND accessible_name IS NOT NULL)
        OR (strategy <> 'role' AND accessible_name IS NULL)
    ),
    CONSTRAINT ui_locator_observations_status_valid CHECK (
        status IN ('unique_visible', 'not_found', 'hidden', 'ambiguous', 'navigation_failed')
    ),
    CONSTRAINT ui_locator_observations_counts_valid CHECK (
        match_count >= 0 AND visible_count BETWEEN 0 AND match_count
    ),
    CONSTRAINT ui_locator_observations_run_fk FOREIGN KEY (
        ui_locator_observation_run_id, project_id
    ) REFERENCES ui_locator_observation_runs(ui_locator_observation_run_id, project_id),
    CONSTRAINT ui_locator_observations_target_fk FOREIGN KEY (
        source_ui_knowledge_snapshot_id, project_id, target_ref
    ) REFERENCES ui_knowledge_targets(ui_knowledge_snapshot_id, project_id, target_ref),
    CONSTRAINT ui_locator_observations_run_candidate_unique UNIQUE (
        ui_locator_observation_run_id, target_ref, locator_candidate_id
    )
);

CREATE INDEX ui_locator_observation_runs_source_idx
    ON ui_locator_observation_runs (
        project_id, source_ui_knowledge_snapshot_id, created_at DESC
    );

CREATE INDEX ui_locator_observations_target_idx
    ON ui_locator_observations (
        project_id, source_ui_knowledge_snapshot_id, target_ref, observed_at DESC
    );
