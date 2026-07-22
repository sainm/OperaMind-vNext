UPDATE ui_execution_runs AS run
SET status = 'blocked', completed_at = COALESCE(run.completed_at, now())
FROM ui_execution_plans AS plan
JOIN edit_results AS edit_result
  ON edit_result.edit_result_id = plan.edit_result_id
 AND edit_result.project_id = plan.project_id
WHERE run.ui_execution_plan_id = plan.ui_execution_plan_id
  AND run.project_id = plan.project_id
  AND run.status = 'running'
  AND edit_result.command_evidence_status = 'legacy_unverified';

UPDATE ui_execution_plans AS plan
SET status = 'blocked',
    blocking_reasons = CASE
        WHEN plan.blocking_reasons ? 'edit_result_command_evidence:legacy_unverified'
        THEN plan.blocking_reasons
        ELSE plan.blocking_reasons
             || '["edit_result_command_evidence:legacy_unverified"]'::jsonb
    END
FROM edit_results AS edit_result
WHERE edit_result.edit_result_id = plan.edit_result_id
  AND edit_result.project_id = plan.project_id
  AND edit_result.command_evidence_status = 'legacy_unverified'
  AND plan.status <> 'completed';
