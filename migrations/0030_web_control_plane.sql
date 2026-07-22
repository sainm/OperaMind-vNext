CREATE TABLE change_requests (
    change_request_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    analysis_case_id text,
    input_mode text NOT NULL,
    submitted_by text NOT NULL,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT change_requests_fields_not_blank CHECK (
        btrim(change_request_id) <> '' AND btrim(submitted_by) <> ''
    ),
    CONSTRAINT change_requests_mode_valid CHECK (
        input_mode IN ('documents', 'natural_language', 'hybrid')
    ),
    CONSTRAINT change_requests_artifact_fk FOREIGN KEY (change_request_id)
        REFERENCES artifact_records(artifact_id),
    CONSTRAINT change_requests_case_fk FOREIGN KEY (analysis_case_id, project_id)
        REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT change_requests_scope_identity_unique UNIQUE (
        change_request_id, project_id
    )
);

CREATE INDEX change_requests_project_submitted_idx
    ON change_requests (project_id, submitted_at DESC, change_request_id DESC);

CREATE TABLE change_request_review_events (
    review_event_id text PRIMARY KEY,
    change_request_id text NOT NULL,
    project_id text NOT NULL,
    review_step text NOT NULL,
    decision text NOT NULL,
    actor text NOT NULL,
    note text,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT change_request_review_events_fields_not_blank CHECK (
        btrim(review_event_id) <> '' AND btrim(actor) <> ''
    ),
    CONSTRAINT change_request_review_events_step_valid CHECK (
        review_step = 'document_diff'
    ),
    CONSTRAINT change_request_review_events_decision_valid CHECK (
        decision IN ('confirmed', 'revision_requested')
    ),
    CONSTRAINT change_request_review_events_digest_sha256 CHECK (
        payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT change_request_review_events_request_fk FOREIGN KEY (
        change_request_id, project_id
    ) REFERENCES change_requests(change_request_id, project_id),
    CONSTRAINT change_request_review_events_scope_identity_unique UNIQUE (
        review_event_id, project_id
    )
);

CREATE INDEX change_request_review_events_request_idx
    ON change_request_review_events (
        project_id, change_request_id, created_at DESC, review_event_id DESC
    );
