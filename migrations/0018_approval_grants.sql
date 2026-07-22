CREATE TABLE approval_grants (
    approval_grant_id text PRIMARY KEY,
    project_id text NOT NULL,
    analysis_case_id text NOT NULL,
    edit_packet_id text NOT NULL,
    impact_report_id text NOT NULL,
    confirmation_id text NOT NULL,
    repository_id text NOT NULL,
    base_repository_revision text NOT NULL,
    editable_files jsonb NOT NULL,
    read_only_files jsonb NOT NULL,
    test_files jsonb NOT NULL,
    allowed_actions jsonb NOT NULL,
    allowed_test_command_refs jsonb NOT NULL,
    allowed_ui_scenarios jsonb NOT NULL,
    forbidden_globs jsonb NOT NULL,
    approved_by text NOT NULL,
    expires_at timestamptz NOT NULL,
    out_of_scope_policy text NOT NULL,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT approval_grants_fields_not_blank CHECK (
        btrim(approval_grant_id) <> ''
        AND btrim(base_repository_revision) <> ''
        AND btrim(approved_by) <> ''
    ),
    CONSTRAINT approval_grants_arrays_valid CHECK (
        jsonb_typeof(editable_files) = 'array'
        AND jsonb_array_length(editable_files) > 0
        AND jsonb_typeof(read_only_files) = 'array'
        AND jsonb_typeof(test_files) = 'array'
        AND jsonb_typeof(allowed_actions) = 'array'
        AND jsonb_array_length(allowed_actions) > 0
        AND jsonb_typeof(allowed_test_command_refs) = 'array'
        AND jsonb_typeof(allowed_ui_scenarios) = 'array'
        AND jsonb_typeof(forbidden_globs) = 'array'
        AND jsonb_array_length(forbidden_globs) > 0
    ),
    CONSTRAINT approval_grants_policy_valid CHECK (
        out_of_scope_policy = 'collect_and_request_once'
    ),
    CONSTRAINT approval_grants_digest_sha256 CHECK (
        payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT approval_grants_expiry_valid CHECK (expires_at > created_at),
    CONSTRAINT approval_grants_packet_fk FOREIGN KEY (
        edit_packet_id, project_id
    ) REFERENCES edit_packets(edit_packet_id, project_id),
    CONSTRAINT approval_grants_case_fk FOREIGN KEY (
        analysis_case_id, project_id
    ) REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT approval_grants_report_fk FOREIGN KEY (
        impact_report_id, project_id
    ) REFERENCES impact_reports(impact_report_id, project_id),
    CONSTRAINT approval_grants_confirmation_fk FOREIGN KEY (
        confirmation_id, project_id
    ) REFERENCES impact_confirmations(confirmation_id, project_id),
    CONSTRAINT approval_grants_repository_fk FOREIGN KEY (
        repository_id, project_id
    ) REFERENCES repositories(repository_id, project_id),
    CONSTRAINT approval_grants_scope_identity_unique UNIQUE (
        approval_grant_id, project_id
    ),
    CONSTRAINT approval_grants_packet_unique UNIQUE (edit_packet_id)
);

CREATE TABLE approval_grant_events (
    approval_grant_event_id text PRIMARY KEY,
    approval_grant_id text NOT NULL,
    project_id text NOT NULL,
    event_type text NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT approval_grant_events_fields_not_blank CHECK (
        btrim(approval_grant_event_id) <> ''
        AND btrim(actor) <> ''
        AND btrim(reason) <> ''
    ),
    CONSTRAINT approval_grant_events_type_valid CHECK (
        event_type IN ('edit_completed', 'completed', 'revoked')
    ),
    CONSTRAINT approval_grant_events_digest_sha256 CHECK (
        payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT approval_grant_events_grant_fk FOREIGN KEY (
        approval_grant_id, project_id
    ) REFERENCES approval_grants(approval_grant_id, project_id),
    CONSTRAINT approval_grant_events_scope_identity_unique UNIQUE (
        approval_grant_event_id, project_id
    )
);

CREATE UNIQUE INDEX approval_grant_events_edit_completed_unique
    ON approval_grant_events (approval_grant_id)
    WHERE event_type = 'edit_completed';

CREATE UNIQUE INDEX approval_grant_events_terminal_unique
    ON approval_grant_events (approval_grant_id)
    WHERE event_type IN ('completed', 'revoked');

CREATE INDEX approval_grants_case_idx
    ON approval_grants (project_id, analysis_case_id, created_at DESC);

ALTER TABLE edit_results
    ADD COLUMN approval_grant_id text,
    ADD CONSTRAINT edit_results_grant_fk FOREIGN KEY (
        approval_grant_id, project_id
    ) REFERENCES approval_grants(approval_grant_id, project_id);

ALTER TABLE ui_execution_runs
    ADD COLUMN approval_grant_id text,
    ADD CONSTRAINT ui_execution_runs_grant_fk FOREIGN KEY (
        approval_grant_id, project_id
    ) REFERENCES approval_grants(approval_grant_id, project_id);
