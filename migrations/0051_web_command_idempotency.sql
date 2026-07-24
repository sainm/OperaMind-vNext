CREATE TABLE web_command_receipts (
    command_scope text NOT NULL,
    idempotency_key text NOT NULL,
    actor text NOT NULL,
    request_digest text NOT NULL,
    response_payload jsonb,
    response_digest text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    PRIMARY KEY (command_scope, idempotency_key),
    CONSTRAINT web_command_receipts_fields_valid CHECK (
        btrim(command_scope) <> ''
        AND btrim(idempotency_key) <> ''
        AND btrim(actor) <> ''
        AND request_digest ~ '^[0-9a-f]{64}$'
        AND (response_payload IS NULL OR jsonb_typeof(response_payload) = 'object')
        AND (response_digest IS NULL OR response_digest ~ '^[0-9a-f]{64}$')
    ),
    CONSTRAINT web_command_receipts_completion_consistent CHECK (
        (response_payload IS NULL AND response_digest IS NULL AND completed_at IS NULL)
        OR
        (response_payload IS NOT NULL AND response_digest IS NOT NULL AND completed_at IS NOT NULL)
    )
);

CREATE INDEX web_command_receipts_created_idx
    ON web_command_receipts (created_at DESC, command_scope);
