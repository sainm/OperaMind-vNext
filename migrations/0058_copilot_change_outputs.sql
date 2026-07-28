ALTER TABLE copilot_coding_task_events
    DROP CONSTRAINT copilot_coding_task_events_type_valid,
    ADD CONSTRAINT copilot_coding_task_events_type_valid CHECK (
        event_type IN (
            'published', 'claimed', 'claim_recovered', 'accepted',
            'context_loaded', 'outputs_recorded', 'command_recorded',
            'diff_recorded', 'result_recorded', 'failed',
            'reanalysis_required', 'cancelled'
        )
    );
