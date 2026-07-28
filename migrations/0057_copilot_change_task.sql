ALTER TABLE copilot_coding_tasks
    DROP CONSTRAINT copilot_coding_tasks_execution_mode_valid,
    ADD CONSTRAINT copilot_coding_tasks_execution_mode_valid CHECK (
        execution_mode IN ('copilot_coding_plan', 'copilot_change_task')
    );
