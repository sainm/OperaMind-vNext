ALTER TABLE copilot_coding_tasks
    ADD COLUMN claim_expires_at timestamptz,
    ADD COLUMN retry_of_coding_task_id text,
    ADD COLUMN attempt_number integer NOT NULL DEFAULT 1;

UPDATE copilot_coding_tasks
SET claim_expires_at = claimed_at + interval '60 seconds'
WHERE claimed_at IS NOT NULL;

ALTER TABLE copilot_coding_tasks
    DROP CONSTRAINT copilot_coding_tasks_claim_consistent,
    ADD CONSTRAINT copilot_coding_tasks_claim_consistent CHECK (
        (claimed_by IS NULL AND claimed_at IS NULL AND claim_expires_at IS NULL)
        OR (
            btrim(claimed_by) <> ''
            AND claimed_at IS NOT NULL
            AND claim_expires_at IS NOT NULL
            AND claim_expires_at > claimed_at
        )
    ),
    ADD CONSTRAINT copilot_coding_tasks_attempt_positive CHECK (
        attempt_number > 0
    ),
    ADD CONSTRAINT copilot_coding_tasks_retry_fk FOREIGN KEY (
        retry_of_coding_task_id, project_id
    ) REFERENCES copilot_coding_tasks(coding_task_id, project_id),
    ADD CONSTRAINT copilot_coding_tasks_retry_not_self CHECK (
        retry_of_coding_task_id IS NULL OR retry_of_coding_task_id <> coding_task_id
    ),
    ADD CONSTRAINT copilot_coding_tasks_retry_unique UNIQUE (
        retry_of_coding_task_id
    );

DROP INDEX copilot_coding_tasks_bridge_queue_idx;

CREATE INDEX copilot_coding_tasks_bridge_queue_idx
    ON copilot_coding_tasks (
        provider_route, workspace_root, state, claim_expires_at,
        created_at, coding_task_id
    );

ALTER TABLE copilot_coding_task_events
    DROP CONSTRAINT copilot_coding_task_events_type_valid,
    ADD CONSTRAINT copilot_coding_task_events_type_valid CHECK (
        event_type IN (
            'published', 'claimed', 'claim_recovered', 'accepted',
            'context_loaded', 'command_recorded', 'diff_recorded',
            'result_recorded', 'failed', 'reanalysis_required',
            'cancelled'
        )
    );
