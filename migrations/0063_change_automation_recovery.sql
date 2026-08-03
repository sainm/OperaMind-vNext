ALTER TABLE change_automation_runs
    DROP CONSTRAINT change_automation_runs_status_valid;

ALTER TABLE change_automation_runs
    ADD CONSTRAINT change_automation_runs_status_valid CHECK (
        status IN ('running', 'waiting', 'blocked', 'failed', 'completed', 'superseded')
    );

ALTER TABLE change_automation_events
    DROP CONSTRAINT change_automation_events_status_valid;

ALTER TABLE change_automation_events
    ADD CONSTRAINT change_automation_events_status_valid CHECK (
        status IN ('running', 'waiting', 'blocked', 'failed', 'completed', 'superseded')
    );

WITH ranked_active_runs AS (
    SELECT automation_run_id,
           row_number() OVER (
               PARTITION BY change_request_id
               ORDER BY updated_at DESC, created_at DESC, automation_run_id DESC
           ) AS position
    FROM change_automation_runs
    WHERE status IN ('running', 'waiting', 'blocked')
)
UPDATE change_automation_runs AS run
SET status = 'superseded', current_stage = 'superseded',
    next_action = NULL, blocking_reason = NULL, updated_at = now()
FROM ranked_active_runs AS ranked
WHERE run.automation_run_id = ranked.automation_run_id
  AND ranked.position > 1;

UPDATE orchestration_task_claims AS claim
SET status = 'released', released_at = now(),
    release_reason = 'automation_run_superseded_by_migration'
FROM orchestration_tasks AS task
JOIN change_automation_runs AS run
  ON run.automation_run_id = task.automation_run_id
 AND run.project_id = task.project_id
WHERE claim.orchestration_task_id = task.orchestration_task_id
  AND claim.status = 'active'
  AND run.status = 'superseded';

UPDATE orchestration_tasks AS task
SET state = 'superseded', updated_at = now()
FROM change_automation_runs AS run
WHERE run.automation_run_id = task.automation_run_id
  AND run.project_id = task.project_id
  AND run.status = 'superseded'
  AND task.state IN ('ready', 'claimed', 'running', 'submitted');

CREATE TABLE change_automation_rag_discoveries (
    automation_run_id text PRIMARY KEY,
    project_id text NOT NULL,
    discovery jsonb NOT NULL,
    subject_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT change_automation_rag_discoveries_object CHECK (
        jsonb_typeof(discovery) = 'object'
        AND discovery ->> 'status' = 'ready'
    ),
    CONSTRAINT change_automation_rag_discoveries_digest_sha256 CHECK (
        subject_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT change_automation_rag_discoveries_run_fk FOREIGN KEY (
        automation_run_id, project_id
    ) REFERENCES change_automation_runs(automation_run_id, project_id)
);
