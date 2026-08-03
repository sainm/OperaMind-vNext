ALTER TABLE project_workspaces
    ADD COLUMN test_base_url text;

ALTER TABLE project_workspaces
    ADD CONSTRAINT project_workspaces_test_base_url_valid CHECK (
        test_base_url IS NULL
        OR test_base_url ~ '^https?://[^[:space:]]+$'
    );

ALTER TABLE copilot_coding_tasks
    DROP CONSTRAINT copilot_coding_tasks_current_stage_valid,
    ADD CONSTRAINT copilot_coding_tasks_current_stage_valid CHECK (
        current_stage IN (
            'document_change', 'code_scope', 'compile_test', 'test_planning',
            'ui_test_revision', 'ui_validation', 'final_report'
        )
    );
