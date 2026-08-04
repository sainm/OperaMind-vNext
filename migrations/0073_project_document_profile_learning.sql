ALTER TABLE project_onboarding_runs
    DROP CONSTRAINT project_onboarding_runs_status_valid,
    DROP CONSTRAINT project_onboarding_runs_stage_valid,
    DROP CONSTRAINT project_onboarding_runs_action_valid;

ALTER TABLE project_onboarding_runs
    ADD CONSTRAINT project_onboarding_runs_status_valid CHECK (
        status IN (
            'queued', 'running', 'waiting_for_profile',
            'ready', 'failed', 'superseded'
        )
    ),
    ADD CONSTRAINT project_onboarding_runs_stage_valid CHECK (
        current_stage IN ('discover', 'learn', 'documents', 'index', 'complete')
    ),
    ADD CONSTRAINT project_onboarding_runs_action_valid CHECK (
        requested_action IN ('initialize', 'rescan', 'reindex', 'relearn')
    );

CREATE TABLE project_document_learning_runs (
    learning_run_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    onboarding_run_id text NOT NULL REFERENCES project_onboarding_runs(onboarding_run_id),
    settings_revision integer NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    requested_by text NOT NULL,
    instruction text,
    source_structure jsonb NOT NULL,
    source_structure_digest text NOT NULL,
    sample_count integer NOT NULL,
    previous_profile_version_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    claimed_by text,
    claim_token_digest text,
    claim_expires_at timestamptz,
    accepted_by text,
    draft_payload jsonb,
    draft_digest text,
    covered_sample_count integer,
    coverage_percent numeric(5,2),
    ambiguity_count integer,
    failure_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    confirmed_at timestamptz,
    confirmed_by text,
    CONSTRAINT project_document_learning_identity_not_blank CHECK (
        btrim(learning_run_id) <> ''
        AND btrim(requested_by) <> ''
        AND btrim(source_structure_digest) <> ''
    ),
    CONSTRAINT project_document_learning_revision_positive CHECK (
        settings_revision > 0
    ),
    CONSTRAINT project_document_learning_status_valid CHECK (
        status IN (
            'pending', 'claimed', 'in_progress', 'draft_ready',
            'confirmed', 'failed', 'cancelled', 'superseded'
        )
    ),
    CONSTRAINT project_document_learning_structure_object CHECK (
        jsonb_typeof(source_structure) = 'object'
    ),
    CONSTRAINT project_document_learning_previous_profiles_array CHECK (
        jsonb_typeof(previous_profile_version_ids) = 'array'
    ),
    CONSTRAINT project_document_learning_digest_sha256 CHECK (
        source_structure_digest ~ '^[0-9a-f]{64}$'
        AND (draft_digest IS NULL OR draft_digest ~ '^[0-9a-f]{64}$')
    ),
    CONSTRAINT project_document_learning_counts_valid CHECK (
        sample_count > 0
        AND (covered_sample_count IS NULL OR covered_sample_count BETWEEN 0 AND sample_count)
        AND (ambiguity_count IS NULL OR ambiguity_count >= 0)
        AND (coverage_percent IS NULL OR coverage_percent BETWEEN 0 AND 100)
    ),
    CONSTRAINT project_document_learning_claim_consistent CHECK (
        (
            status IN ('claimed', 'in_progress')
            AND claimed_by IS NOT NULL
            AND claim_token_digest IS NOT NULL
            AND claim_expires_at IS NOT NULL
        )
        OR
        (
            status NOT IN ('claimed', 'in_progress')
            AND claimed_by IS NULL
            AND claim_token_digest IS NULL
            AND claim_expires_at IS NULL
        )
    ),
    CONSTRAINT project_document_learning_draft_consistent CHECK (
        status NOT IN ('draft_ready', 'confirmed')
        OR (
            draft_payload IS NOT NULL
            AND jsonb_typeof(draft_payload) = 'object'
            AND draft_digest IS NOT NULL
            AND covered_sample_count IS NOT NULL
            AND coverage_percent IS NOT NULL
            AND ambiguity_count IS NOT NULL
        )
    ),
    CONSTRAINT project_document_learning_confirmation_consistent CHECK (
        (status = 'confirmed' AND confirmed_at IS NOT NULL AND confirmed_by IS NOT NULL)
        OR (status <> 'confirmed' AND confirmed_at IS NULL AND confirmed_by IS NULL)
    ),
    CONSTRAINT project_document_learning_scope_unique UNIQUE (
        learning_run_id, project_id
    )
);

CREATE TABLE project_document_learning_profiles (
    learning_run_id text NOT NULL,
    project_id text NOT NULL,
    profile_version_id text NOT NULL REFERENCES profile_versions(profile_version_id),
    position integer NOT NULL,
    PRIMARY KEY (learning_run_id, profile_version_id),
    CONSTRAINT project_document_learning_profiles_position_nonnegative CHECK (position >= 0),
    CONSTRAINT project_document_learning_profiles_scope_fk FOREIGN KEY (
        learning_run_id, project_id
    ) REFERENCES project_document_learning_runs(learning_run_id, project_id),
    CONSTRAINT project_document_learning_profiles_position_unique UNIQUE (
        learning_run_id, position
    )
);

ALTER TABLE project_onboarding_runs
    ADD COLUMN learning_run_id text,
    ADD CONSTRAINT project_onboarding_runs_learning_fk FOREIGN KEY (
        learning_run_id, project_id
    ) REFERENCES project_document_learning_runs(learning_run_id, project_id),
    ADD CONSTRAINT project_onboarding_runs_learning_stage_consistent CHECK (
        (status = 'waiting_for_profile' AND current_stage = 'learn' AND learning_run_id IS NOT NULL)
        OR status <> 'waiting_for_profile'
    );

CREATE UNIQUE INDEX project_document_learning_active_revision_unique
    ON project_document_learning_runs (project_id, settings_revision)
    WHERE status IN ('pending', 'claimed', 'in_progress', 'draft_ready');

CREATE INDEX project_document_learning_bridge_queue_idx
    ON project_document_learning_runs (status, created_at, learning_run_id)
    WHERE status IN ('pending', 'claimed', 'in_progress');

CREATE INDEX project_document_learning_project_latest_idx
    ON project_document_learning_runs (project_id, created_at DESC, learning_run_id DESC);
