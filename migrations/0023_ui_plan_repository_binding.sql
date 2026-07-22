ALTER TABLE ui_execution_plans
    ADD COLUMN repository_binding_status text;

UPDATE ui_execution_plans AS plan
SET repository_binding_status = CASE
    WHEN plan.repository_revision = result.result_repository_revision
     AND plan.repository_revision = deployment.repository_revision
    THEN 'verified'
    ELSE 'legacy_invalid'
END
FROM edit_results AS result,
     ui_deployments AS deployment
WHERE result.edit_result_id = plan.edit_result_id
  AND result.project_id = plan.project_id
  AND deployment.environment_id = plan.environment_id
  AND deployment.deployment_revision = plan.deployment_revision
  AND deployment.project_id = plan.project_id;

ALTER TABLE ui_execution_plans
    ALTER COLUMN repository_binding_status SET NOT NULL,
    ADD CONSTRAINT ui_execution_plans_repository_binding_status_valid CHECK (
        repository_binding_status IN ('verified', 'legacy_invalid')
    );

UPDATE ui_execution_plans
SET status = 'blocked',
    blocking_reasons = CASE
        WHEN blocking_reasons @> '["ui_plan_repository_binding:legacy_invalid"]'::jsonb
        THEN blocking_reasons
        ELSE blocking_reasons || '["ui_plan_repository_binding:legacy_invalid"]'::jsonb
    END
WHERE repository_binding_status = 'legacy_invalid'
  AND status <> 'completed';

UPDATE ui_execution_runs AS run
SET status = 'blocked', completed_at = now()
FROM ui_execution_plans AS plan
WHERE plan.ui_execution_plan_id = run.ui_execution_plan_id
  AND plan.project_id = run.project_id
  AND plan.repository_binding_status = 'legacy_invalid'
  AND run.status = 'running';

CREATE INDEX ui_execution_plans_repository_binding_idx
    ON ui_execution_plans (project_id, repository_binding_status, status);
