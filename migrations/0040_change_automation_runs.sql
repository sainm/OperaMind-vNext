CREATE TABLE change_automation_runs (
    automation_run_id text PRIMARY KEY,
    change_request_id text NOT NULL,
    project_id text NOT NULL,
    idempotency_key text NOT NULL,
    status text NOT NULL,
    current_stage text NOT NULL,
    next_action text,
    blocking_reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT change_automation_runs_fields_not_blank CHECK (
        btrim(automation_run_id) <> ''
        AND btrim(idempotency_key) <> ''
        AND btrim(current_stage) <> ''
        AND btrim(created_by) <> ''
        AND (next_action IS NULL OR btrim(next_action) <> '')
        AND (blocking_reason IS NULL OR btrim(blocking_reason) <> '')
    ),
    CONSTRAINT change_automation_runs_status_valid CHECK (
        status IN ('running', 'waiting', 'blocked', 'failed', 'completed')
    ),
    CONSTRAINT change_automation_runs_request_fk FOREIGN KEY (
        change_request_id, project_id
    ) REFERENCES change_requests(change_request_id, project_id),
    CONSTRAINT change_automation_runs_scope_unique UNIQUE (
        automation_run_id, project_id
    ),
    CONSTRAINT change_automation_runs_idempotency_unique UNIQUE (
        change_request_id, idempotency_key
    )
);

CREATE TABLE change_automation_events (
    event_id text PRIMARY KEY,
    automation_run_id text NOT NULL,
    project_id text NOT NULL,
    sequence integer NOT NULL,
    event_type text NOT NULL,
    stage text NOT NULL,
    status text NOT NULL,
    actor text NOT NULL,
    message text NOT NULL,
    artifact_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT change_automation_events_fields_not_blank CHECK (
        btrim(event_id) <> ''
        AND sequence >= 1
        AND btrim(event_type) <> ''
        AND btrim(stage) <> ''
        AND btrim(actor) <> ''
        AND btrim(message) <> ''
    ),
    CONSTRAINT change_automation_events_status_valid CHECK (
        status IN ('running', 'waiting', 'blocked', 'failed', 'completed')
    ),
    CONSTRAINT change_automation_events_digest_sha256 CHECK (
        payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT change_automation_events_refs_array CHECK (
        jsonb_typeof(artifact_refs) = 'array'
    ),
    CONSTRAINT change_automation_events_run_fk FOREIGN KEY (
        automation_run_id, project_id
    ) REFERENCES change_automation_runs(automation_run_id, project_id),
    CONSTRAINT change_automation_events_sequence_unique UNIQUE (
        automation_run_id, sequence
    )
);

CREATE INDEX change_automation_runs_request_idx
    ON change_automation_runs (
        project_id, change_request_id, updated_at DESC, automation_run_id DESC
    );

CREATE INDEX change_automation_events_run_idx
    ON change_automation_events (project_id, automation_run_id, sequence);
