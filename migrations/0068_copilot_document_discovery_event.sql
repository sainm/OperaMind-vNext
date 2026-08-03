ALTER TABLE copilot_coding_task_events
    DROP CONSTRAINT copilot_coding_task_events_type_valid,
    ADD CONSTRAINT copilot_coding_task_events_type_valid CHECK (
        event_type IN (
            'published', 'claimed', 'claim_recovered', 'accepted',
            'context_loaded', 'document_discovery_bound', 'outputs_recorded',
            'scope_bound', 'command_recorded', 'diff_recorded',
            'result_recorded', 'failed', 'reanalysis_required', 'cancelled'
        )
    );
