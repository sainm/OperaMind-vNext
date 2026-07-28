ALTER TABLE copilot_coding_tasks
    ALTER COLUMN analysis_case_id DROP NOT NULL,
    ALTER COLUMN repository_id DROP NOT NULL,
    ALTER COLUMN edit_packet_id DROP NOT NULL,
    ALTER COLUMN approval_grant_id DROP NOT NULL,
    ALTER COLUMN base_repository_revision DROP NOT NULL,
    ADD COLUMN current_stage text NOT NULL DEFAULT 'compile_test',
    ADD CONSTRAINT copilot_coding_tasks_current_stage_valid CHECK (
        current_stage IN (
            'document_change', 'code_scope', 'compile_test',
            'ui_validation', 'final_report'
        )
    ),
    ADD CONSTRAINT copilot_coding_tasks_scope_consistent CHECK (
        (
            analysis_case_id IS NULL
            AND repository_id IS NULL
            AND edit_packet_id IS NULL
            AND approval_grant_id IS NULL
            AND base_repository_revision IS NULL
        )
        OR (
            analysis_case_id IS NOT NULL
            AND repository_id IS NOT NULL
            AND edit_packet_id IS NOT NULL
            AND approval_grant_id IS NOT NULL
            AND base_repository_revision IS NOT NULL
        )
    );

ALTER TABLE copilot_coding_task_events
    DROP CONSTRAINT copilot_coding_task_events_type_valid,
    ADD CONSTRAINT copilot_coding_task_events_type_valid CHECK (
        event_type IN (
            'published', 'claimed', 'claim_recovered', 'accepted',
            'context_loaded', 'outputs_recorded', 'scope_bound',
            'command_recorded', 'diff_recorded', 'result_recorded',
            'failed', 'reanalysis_required', 'cancelled'
        )
    );
