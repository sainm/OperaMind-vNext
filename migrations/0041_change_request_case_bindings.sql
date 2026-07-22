CREATE TABLE change_request_case_binding_events (
    binding_event_id text PRIMARY KEY,
    change_request_id text NOT NULL,
    project_id text NOT NULL,
    analysis_case_id text NOT NULL,
    actor text NOT NULL,
    idempotency_key text NOT NULL,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT change_request_case_binding_fields_not_blank CHECK (
        btrim(binding_event_id) <> ''
        AND btrim(actor) <> ''
        AND btrim(idempotency_key) <> ''
    ),
    CONSTRAINT change_request_case_binding_digest_sha256 CHECK (
        payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT change_request_case_binding_request_fk FOREIGN KEY (
        change_request_id, project_id
    ) REFERENCES change_requests(change_request_id, project_id),
    CONSTRAINT change_request_case_binding_case_fk FOREIGN KEY (
        analysis_case_id, project_id
    ) REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT change_request_case_binding_idempotency_unique UNIQUE (
        change_request_id, idempotency_key
    ),
    CONSTRAINT change_request_case_binding_one_case_unique UNIQUE (
        change_request_id
    )
);
