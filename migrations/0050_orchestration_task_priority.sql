ALTER TABLE orchestration_tasks
    ADD COLUMN priority integer NOT NULL DEFAULT 100,
    ADD CONSTRAINT orchestration_tasks_priority_valid CHECK (priority BETWEEN 1 AND 1000);

CREATE INDEX orchestration_tasks_ready_priority_idx
    ON orchestration_tasks (priority DESC, created_at, automation_run_id, sequence)
    WHERE state = 'ready';

ALTER TABLE orchestration_task_events
    DROP CONSTRAINT orchestration_task_events_type_valid,
    ADD CONSTRAINT orchestration_task_events_type_valid CHECK (
        event_type IN (
            'created', 'claimed', 'started', 'lease_renewed', 'released',
            'lease_expired', 'requeued', 'result_submitted', 'completed', 'failed',
            'blocked', 'superseded', 'priority_updated'
        )
    );
