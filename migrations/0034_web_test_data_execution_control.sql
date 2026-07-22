ALTER TABLE test_data_execution_runs
    ADD COLUMN replay_of_run_id text,
    ADD CONSTRAINT test_data_execution_runs_replay_not_self CHECK (
        replay_of_run_id IS NULL OR replay_of_run_id <> run_id
    ),
    ADD CONSTRAINT test_data_execution_runs_replay_fk FOREIGN KEY (
        replay_of_run_id, project_id
    ) REFERENCES test_data_execution_runs(run_id, project_id);

CREATE TABLE test_data_execution_events (
    event_id text PRIMARY KEY,
    run_id text NOT NULL,
    project_id text NOT NULL,
    sequence integer NOT NULL,
    event_type text NOT NULL,
    flow_id text,
    phase text,
    step_id text,
    status text,
    message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT test_data_execution_events_fields_valid CHECK (
        btrim(event_id) <> ''
        AND sequence >= 1
        AND btrim(event_type) <> ''
        AND (flow_id IS NULL OR btrim(flow_id) <> '')
        AND (step_id IS NULL OR btrim(step_id) <> '')
        AND (message IS NULL OR btrim(message) <> '')
    ),
    CONSTRAINT test_data_execution_events_phase_valid CHECK (
        phase IS NULL OR phase IN ('setup', 'cleanup')
    ),
    CONSTRAINT test_data_execution_events_status_valid CHECK (
        status IS NULL OR status IN (
            'running', 'passed', 'failed', 'blocked', 'not_run', 'interrupted'
        )
    ),
    CONSTRAINT test_data_execution_events_step_scope_valid CHECK (
        step_id IS NULL OR (flow_id IS NOT NULL AND phase IS NOT NULL)
    ),
    CONSTRAINT test_data_execution_events_run_fk FOREIGN KEY (run_id, project_id)
        REFERENCES test_data_execution_runs(run_id, project_id),
    CONSTRAINT test_data_execution_events_sequence_unique UNIQUE (run_id, sequence)
);

CREATE TABLE test_data_execution_recoveries (
    recovery_id text PRIMARY KEY,
    run_id text NOT NULL,
    project_id text NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL,
    stale_before timestamptz NOT NULL,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT test_data_execution_recoveries_fields_not_blank CHECK (
        btrim(recovery_id) <> ''
        AND btrim(actor) <> ''
        AND btrim(reason) <> ''
    ),
    CONSTRAINT test_data_execution_recoveries_digest_sha256 CHECK (
        payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT test_data_execution_recoveries_run_fk FOREIGN KEY (run_id, project_id)
        REFERENCES test_data_execution_runs(run_id, project_id),
    CONSTRAINT test_data_execution_recoveries_run_unique UNIQUE (run_id)
);

CREATE INDEX test_data_execution_events_run_idx
    ON test_data_execution_events (project_id, run_id, sequence);
