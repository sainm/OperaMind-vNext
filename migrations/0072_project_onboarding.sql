ALTER TABLE project_workspaces
    ADD COLUMN settings_revision integer NOT NULL DEFAULT 1,
    ADD COLUMN updated_by text,
    ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
    ADD CONSTRAINT project_workspaces_settings_revision_positive CHECK (
        settings_revision > 0
    ),
    ADD CONSTRAINT project_workspaces_updated_by_not_blank CHECK (
        updated_by IS NULL OR btrim(updated_by) <> ''
    );

CREATE TABLE project_onboarding_runs (
    onboarding_run_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    settings_revision integer NOT NULL,
    requested_action text NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    current_stage text NOT NULL,
    requested_by text NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0,
    lease_owner text,
    lease_token_digest text,
    lease_expires_at timestamptz,
    heartbeat_at timestamptz,
    document_snapshot_id text,
    document_count integer,
    search_index_build_id text,
    generated_vector_count integer,
    failure_reason text,
    CONSTRAINT project_onboarding_runs_identity_not_blank CHECK (
        btrim(onboarding_run_id) <> ''
        AND btrim(requested_by) <> ''
    ),
    CONSTRAINT project_onboarding_runs_action_valid CHECK (
        requested_action IN ('initialize', 'rescan', 'reindex')
    ),
    CONSTRAINT project_onboarding_runs_status_valid CHECK (
        status IN ('queued', 'running', 'ready', 'failed', 'superseded')
    ),
    CONSTRAINT project_onboarding_runs_stage_valid CHECK (
        current_stage IN ('discover', 'documents', 'index', 'complete')
    ),
    CONSTRAINT project_onboarding_runs_revision_positive CHECK (settings_revision > 0),
    CONSTRAINT project_onboarding_runs_attempt_nonnegative CHECK (attempt_count >= 0),
    CONSTRAINT project_onboarding_runs_document_count_nonnegative CHECK (
        document_count IS NULL OR document_count >= 0
    ),
    CONSTRAINT project_onboarding_runs_vector_count_nonnegative CHECK (
        generated_vector_count IS NULL OR generated_vector_count >= 0
    ),
    CONSTRAINT project_onboarding_runs_lease_consistent CHECK (
        (status = 'running'
            AND lease_owner IS NOT NULL
            AND lease_token_digest IS NOT NULL
            AND lease_expires_at IS NOT NULL
            AND heartbeat_at IS NOT NULL)
        OR
        (status <> 'running'
            AND lease_owner IS NULL
            AND lease_token_digest IS NULL
            AND lease_expires_at IS NULL
            AND heartbeat_at IS NULL)
    ),
    CONSTRAINT project_onboarding_runs_ready_consistent CHECK (
        status <> 'ready'
        OR (
            current_stage = 'complete'
            AND document_snapshot_id IS NOT NULL
            AND document_count IS NOT NULL
            AND search_index_build_id IS NOT NULL
            AND generated_vector_count IS NOT NULL
            AND completed_at IS NOT NULL
        )
    )
);

CREATE INDEX project_onboarding_runs_project_latest_idx
    ON project_onboarding_runs (project_id, requested_at DESC, onboarding_run_id DESC);

CREATE INDEX project_onboarding_runs_claimable_idx
    ON project_onboarding_runs (status, lease_expires_at, requested_at)
    WHERE status IN ('queued', 'running');
