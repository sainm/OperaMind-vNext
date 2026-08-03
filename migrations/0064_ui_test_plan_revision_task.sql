ALTER TABLE copilot_coding_tasks
    DROP CONSTRAINT copilot_coding_tasks_current_stage_valid,
    ADD CONSTRAINT copilot_coding_tasks_current_stage_valid CHECK (
        current_stage IN (
            'document_change', 'code_scope', 'compile_test',
            'ui_test_revision', 'ui_validation', 'final_report'
        )
    );
