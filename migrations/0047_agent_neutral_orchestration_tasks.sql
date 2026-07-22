CREATE TABLE orchestration_tasks (
    orchestration_task_id text PRIMARY KEY,
    protocol_version text NOT NULL,
    automation_run_id text NOT NULL,
    change_request_id text NOT NULL,
    project_id text NOT NULL,
    sequence integer NOT NULL,
    step_key text NOT NULL,
    action text NOT NULL,
    title text NOT NULL,
    instruction text NOT NULL,
    task_kind text NOT NULL,
    state text NOT NULL DEFAULT 'ready',
    required_capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
    eligible_executor_kinds jsonb NOT NULL DEFAULT '["agent", "subagent", "human"]'::jsonb,
    input_artifact_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    expected_output_types jsonb NOT NULL DEFAULT '[]'::jsonb,
    acceptance_criteria jsonb NOT NULL DEFAULT '[]'::jsonb,
    lease_seconds integer NOT NULL,
    max_attempts integer NOT NULL,
    attempt_count integer NOT NULL DEFAULT 0,
    definition_digest text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT orchestration_tasks_fields_not_blank CHECK (
        btrim(orchestration_task_id) <> ''
        AND btrim(protocol_version) <> ''
        AND sequence >= 1
        AND btrim(step_key) <> ''
        AND btrim(action) <> ''
        AND btrim(title) <> ''
        AND btrim(instruction) <> ''
        AND btrim(task_kind) <> ''
        AND btrim(created_by) <> ''
    ),
    CONSTRAINT orchestration_tasks_kind_valid CHECK (
        task_kind IN (
            'deterministic_action', 'judgment', 'external_execution',
            'verification', 'recovery'
        )
    ),
    CONSTRAINT orchestration_tasks_state_valid CHECK (
        state IN (
            'ready', 'claimed', 'running', 'submitted', 'completed', 'failed',
            'blocked', 'cancelled', 'superseded'
        )
    ),
    CONSTRAINT orchestration_tasks_json_arrays CHECK (
        jsonb_typeof(required_capabilities) = 'array'
        AND jsonb_typeof(eligible_executor_kinds) = 'array'
        AND jsonb_typeof(input_artifact_refs) = 'array'
        AND jsonb_typeof(expected_output_types) = 'array'
        AND jsonb_typeof(acceptance_criteria) = 'array'
    ),
    CONSTRAINT orchestration_tasks_executor_neutral CHECK (
        eligible_executor_kinds = '["agent", "subagent", "human"]'::jsonb
        AND jsonb_array_length(required_capabilities) > 0
        AND jsonb_array_length(expected_output_types) > 0
        AND jsonb_array_length(acceptance_criteria) > 0
    ),
    CONSTRAINT orchestration_tasks_execution_policy CHECK (
        lease_seconds BETWEEN 30 AND 86400
        AND max_attempts BETWEEN 1 AND 100
        AND attempt_count BETWEEN 0 AND max_attempts
    ),
    CONSTRAINT orchestration_tasks_digest_sha256 CHECK (
        definition_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT orchestration_tasks_run_fk FOREIGN KEY (
        automation_run_id, project_id
    ) REFERENCES change_automation_runs(automation_run_id, project_id),
    CONSTRAINT orchestration_tasks_request_fk FOREIGN KEY (
        change_request_id, project_id
    ) REFERENCES change_requests(change_request_id, project_id),
    CONSTRAINT orchestration_tasks_scope_unique UNIQUE (
        orchestration_task_id, project_id
    ),
    CONSTRAINT orchestration_tasks_step_unique UNIQUE (
        automation_run_id, step_key, action, definition_digest
    ),
    CONSTRAINT orchestration_tasks_sequence_unique UNIQUE (
        automation_run_id, sequence
    )
);

CREATE TABLE orchestration_task_dependencies (
    orchestration_task_id text NOT NULL,
    depends_on_task_id text NOT NULL,
    project_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (orchestration_task_id, depends_on_task_id),
    CONSTRAINT orchestration_task_dependencies_not_self CHECK (
        orchestration_task_id <> depends_on_task_id
    ),
    CONSTRAINT orchestration_task_dependencies_task_fk FOREIGN KEY (
        orchestration_task_id, project_id
    ) REFERENCES orchestration_tasks(orchestration_task_id, project_id),
    CONSTRAINT orchestration_task_dependencies_parent_fk FOREIGN KEY (
        depends_on_task_id, project_id
    ) REFERENCES orchestration_tasks(orchestration_task_id, project_id)
);

CREATE TABLE orchestration_task_claims (
    claim_id text PRIMARY KEY,
    orchestration_task_id text NOT NULL,
    project_id text NOT NULL,
    executor_kind text NOT NULL,
    executor_id text NOT NULL,
    capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
    lease_token_digest text NOT NULL,
    status text NOT NULL,
    claimed_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz NOT NULL,
    released_at timestamptz,
    release_reason text,
    CONSTRAINT orchestration_task_claims_fields_not_blank CHECK (
        btrim(claim_id) <> ''
        AND btrim(executor_id) <> ''
        AND (release_reason IS NULL OR btrim(release_reason) <> '')
    ),
    CONSTRAINT orchestration_task_claims_executor_valid CHECK (
        executor_kind IN ('agent', 'subagent', 'human')
    ),
    CONSTRAINT orchestration_task_claims_status_valid CHECK (
        status IN ('active', 'completed', 'released', 'expired')
    ),
    CONSTRAINT orchestration_task_claims_capabilities_array CHECK (
        jsonb_typeof(capabilities) = 'array'
    ),
    CONSTRAINT orchestration_task_claims_digest_sha256 CHECK (
        lease_token_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT orchestration_task_claims_lease_valid CHECK (
        lease_expires_at > claimed_at
        AND (
            (status = 'active' AND released_at IS NULL AND release_reason IS NULL)
            OR (status <> 'active' AND released_at IS NOT NULL)
        )
    ),
    CONSTRAINT orchestration_task_claims_task_fk FOREIGN KEY (
        orchestration_task_id, project_id
    ) REFERENCES orchestration_tasks(orchestration_task_id, project_id),
    CONSTRAINT orchestration_task_claims_scope_unique UNIQUE (
        claim_id, orchestration_task_id, project_id
    )
);

CREATE UNIQUE INDEX orchestration_task_claims_one_active_idx
    ON orchestration_task_claims (orchestration_task_id)
    WHERE status = 'active';

CREATE TABLE orchestration_task_results (
    result_id text PRIMARY KEY,
    orchestration_task_id text NOT NULL,
    project_id text NOT NULL,
    claim_id text NOT NULL UNIQUE,
    outcome text NOT NULL,
    summary text NOT NULL,
    artifact_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload_digest text NOT NULL,
    submitted_by text NOT NULL,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT orchestration_task_results_fields_not_blank CHECK (
        btrim(result_id) <> ''
        AND btrim(summary) <> ''
        AND btrim(submitted_by) <> ''
    ),
    CONSTRAINT orchestration_task_results_outcome_valid CHECK (
        outcome IN ('completed', 'failed', 'blocked')
    ),
    CONSTRAINT orchestration_task_results_json_valid CHECK (
        jsonb_typeof(artifact_refs) = 'array'
        AND jsonb_typeof(evidence) = 'object'
    ),
    CONSTRAINT orchestration_task_results_digest_sha256 CHECK (
        payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT orchestration_task_results_task_fk FOREIGN KEY (
        orchestration_task_id, project_id
    ) REFERENCES orchestration_tasks(orchestration_task_id, project_id),
    CONSTRAINT orchestration_task_results_claim_fk FOREIGN KEY (
        claim_id, orchestration_task_id, project_id
    ) REFERENCES orchestration_task_claims(
        claim_id, orchestration_task_id, project_id
    )
);

CREATE TABLE orchestration_task_events (
    event_id text PRIMARY KEY,
    orchestration_task_id text NOT NULL,
    project_id text NOT NULL,
    sequence integer NOT NULL,
    event_type text NOT NULL,
    actor text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT orchestration_task_events_fields_not_blank CHECK (
        btrim(event_id) <> ''
        AND sequence >= 1
        AND btrim(event_type) <> ''
        AND btrim(actor) <> ''
    ),
    CONSTRAINT orchestration_task_events_type_valid CHECK (
        event_type IN (
            'created', 'claimed', 'started', 'lease_renewed', 'released',
            'lease_expired', 'requeued', 'result_submitted', 'completed', 'failed',
            'blocked', 'superseded'
        )
    ),
    CONSTRAINT orchestration_task_events_payload_object CHECK (
        jsonb_typeof(payload) = 'object'
    ),
    CONSTRAINT orchestration_task_events_digest_sha256 CHECK (
        payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT orchestration_task_events_task_fk FOREIGN KEY (
        orchestration_task_id, project_id
    ) REFERENCES orchestration_tasks(orchestration_task_id, project_id),
    CONSTRAINT orchestration_task_events_sequence_unique UNIQUE (
        orchestration_task_id, sequence
    )
);

CREATE INDEX orchestration_tasks_ready_idx
    ON orchestration_tasks (project_id, state, created_at, orchestration_task_id);

CREATE INDEX orchestration_tasks_run_idx
    ON orchestration_tasks (automation_run_id, sequence);

CREATE INDEX orchestration_task_events_task_idx
    ON orchestration_task_events (orchestration_task_id, sequence);
