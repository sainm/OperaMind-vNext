ALTER TABLE edit_results
    ADD COLUMN command_evidence_status text NOT NULL DEFAULT 'legacy_unverified';

UPDATE edit_results AS edit_result
SET command_evidence_status = CASE
    WHEN edit_result.validation_mode = 'working' THEN 'not_applicable'
    WHEN jsonb_array_length(edit_result.test_result_refs) > 0
         AND (
             SELECT count(*)
             FROM edit_result_command_executions AS relation
             WHERE relation.edit_result_id = edit_result.edit_result_id
               AND relation.project_id = edit_result.project_id
         ) = jsonb_array_length(edit_result.test_result_refs)
         AND (
             (
                 edit_result.tests_passed
                 AND NOT EXISTS (
                     SELECT 1
                     FROM edit_result_command_executions AS relation
                     JOIN command_execution_results AS result
                       ON result.command_execution_id = relation.command_execution_id
                     WHERE relation.edit_result_id = edit_result.edit_result_id
                       AND relation.project_id = edit_result.project_id
                       AND result.status <> 'passed'
                 )
             )
             OR (
                 NOT edit_result.tests_passed
                 AND EXISTS (
                     SELECT 1
                     FROM edit_result_command_executions AS relation
                     JOIN command_execution_results AS result
                       ON result.command_execution_id = relation.command_execution_id
                     WHERE relation.edit_result_id = edit_result.edit_result_id
                       AND relation.project_id = edit_result.project_id
                       AND result.status <> 'passed'
                 )
             )
         )
    THEN 'verified'
    ELSE 'legacy_unverified'
END;

ALTER TABLE edit_results
    ALTER COLUMN command_evidence_status DROP DEFAULT,
    ADD CONSTRAINT edit_results_command_evidence_status_valid CHECK (
        command_evidence_status IN ('not_applicable', 'verified', 'legacy_unverified')
    ),
    ADD CONSTRAINT edit_results_command_evidence_mode_consistent CHECK (
        (validation_mode = 'working' AND command_evidence_status = 'not_applicable')
        OR (
            validation_mode = 'committed'
            AND command_evidence_status IN ('verified', 'legacy_unverified')
        )
    );
