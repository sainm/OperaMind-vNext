ALTER TABLE test_data_execution_runs
    ADD COLUMN execution_owner text,
    ADD COLUMN heartbeat_at timestamptz,
    ADD COLUMN lease_expires_at timestamptz,
    ADD COLUMN attempt_count integer NOT NULL DEFAULT 0,
    ADD COLUMN max_attempts integer NOT NULL DEFAULT 3,
    ADD CONSTRAINT test_data_execution_runs_attempts_valid CHECK (
        attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts
    ),
    ADD CONSTRAINT test_data_execution_runs_lease_valid CHECK (
        (
            execution_owner IS NULL
            AND heartbeat_at IS NULL
            AND lease_expires_at IS NULL
        )
        OR (
            btrim(execution_owner) <> ''
            AND heartbeat_at IS NOT NULL
            AND lease_expires_at > heartbeat_at
            AND status = 'running'
        )
    );

CREATE INDEX test_data_execution_runs_running_lease_idx
    ON test_data_execution_runs (lease_expires_at, run_id)
    WHERE status = 'running';
