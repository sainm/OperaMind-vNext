WITH ranked AS (
    SELECT candidate.onboarding_run_id,
           row_number() OVER (
               PARTITION BY candidate.project_id, candidate.settings_revision
               ORDER BY EXISTS (
                            SELECT 1
                            FROM project_document_learning_runs AS learning
                            WHERE learning.onboarding_run_id = candidate.onboarding_run_id
                              AND learning.status IN (
                                  'pending', 'claimed', 'in_progress', 'draft_ready'
                              )
                        ) DESC,
                        (candidate.status = 'waiting_for_profile') DESC,
                        candidate.updated_at DESC,
                        candidate.requested_at DESC,
                        candidate.onboarding_run_id DESC
           ) AS position
    FROM project_onboarding_runs AS candidate
    WHERE candidate.status IN ('queued', 'running', 'waiting_for_profile')
)
UPDATE project_onboarding_runs AS run
SET status = 'superseded',
    completed_at = clock_timestamp(),
    lease_owner = NULL,
    lease_token_digest = NULL,
    lease_expires_at = NULL,
    heartbeat_at = NULL,
    failure_reason = '重複した Project Onboarding Run を統合しました',
    updated_at = clock_timestamp()
FROM ranked
WHERE ranked.onboarding_run_id = run.onboarding_run_id
  AND ranked.position > 1;

CREATE UNIQUE INDEX project_onboarding_active_revision_unique
    ON project_onboarding_runs (project_id, settings_revision)
    WHERE status IN ('queued', 'running', 'waiting_for_profile');
