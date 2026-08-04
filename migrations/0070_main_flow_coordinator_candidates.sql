CREATE INDEX change_automation_runs_coordinator_candidates_idx
    ON change_automation_runs (
        status, next_action, updated_at, automation_run_id, change_request_id
    )
    WHERE status IN ('running', 'waiting');

CREATE INDEX orchestration_task_claims_active_expiry_idx
    ON orchestration_task_claims (lease_expires_at, orchestration_task_id)
    WHERE status = 'active';
