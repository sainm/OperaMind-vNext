ALTER TABLE orchestration_worker_registrations
    ADD COLUMN credential_digest text;

UPDATE orchestration_worker_registrations
SET credential_digest = repeat('0', 64),
    status = 'offline',
    updated_at = now();

ALTER TABLE orchestration_worker_registrations
    ALTER COLUMN credential_digest SET NOT NULL,
    ADD CONSTRAINT orchestration_worker_registrations_credential_sha256 CHECK (
        credential_digest ~ '^[0-9a-f]{64}$'
    );

CREATE TABLE orchestration_worker_events (
    executor_kind text NOT NULL,
    executor_id text NOT NULL,
    sequence integer NOT NULL,
    event_type text NOT NULL,
    actor text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (executor_kind, executor_id, sequence),
    CONSTRAINT orchestration_worker_events_registration_fk FOREIGN KEY (
        executor_kind, executor_id
    ) REFERENCES orchestration_worker_registrations(executor_kind, executor_id),
    CONSTRAINT orchestration_worker_events_fields_valid CHECK (
        btrim(actor) <> ''
        AND event_type IN (
            'registered', 'enabled', 'disabled', 'drain_requested',
            'configuration_updated'
        )
        AND jsonb_typeof(payload) = 'object'
        AND payload_digest ~ '^[0-9a-f]{64}$'
    )
);

CREATE INDEX orchestration_worker_events_created_idx
    ON orchestration_worker_events (created_at DESC, executor_kind, executor_id);
