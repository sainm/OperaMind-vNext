CREATE TABLE orchestration_worker_registrations (
    executor_kind text NOT NULL,
    executor_id text NOT NULL,
    capabilities jsonb NOT NULL,
    project_id text,
    max_concurrent_tasks integer NOT NULL DEFAULT 1,
    status text NOT NULL DEFAULT 'online',
    registered_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (executor_kind, executor_id),
    CONSTRAINT orchestration_worker_registrations_fields_valid CHECK (
        executor_kind IN ('agent', 'subagent')
        AND btrim(executor_id) <> ''
        AND (project_id IS NULL OR btrim(project_id) <> '')
    ),
    CONSTRAINT orchestration_worker_registrations_capabilities_valid CHECK (
        jsonb_typeof(capabilities) = 'array'
        AND jsonb_array_length(capabilities) > 0
    ),
    CONSTRAINT orchestration_worker_registrations_concurrency_valid CHECK (
        max_concurrent_tasks BETWEEN 1 AND 100
    ),
    CONSTRAINT orchestration_worker_registrations_status_valid CHECK (
        status IN ('online', 'draining', 'offline')
    ),
    CONSTRAINT orchestration_worker_registrations_lease_valid CHECK (
        lease_expires_at > last_seen_at
    )
);

CREATE INDEX orchestration_worker_registrations_live_idx
    ON orchestration_worker_registrations (status, lease_expires_at, executor_kind);

CREATE INDEX orchestration_task_claims_executor_idx
    ON orchestration_task_claims (executor_kind, executor_id, status, lease_expires_at);
