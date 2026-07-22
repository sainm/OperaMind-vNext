ALTER TABLE command_execution_results
    DROP CONSTRAINT command_execution_results_status_valid,
    DROP CONSTRAINT command_execution_results_exit_consistent;

ALTER TABLE command_execution_results
    ADD COLUMN recovery_id text,
    ADD COLUMN recovery_actor text,
    ADD COLUMN recovery_reason text,
    ADD COLUMN recovery_stale_before timestamptz,
    ADD CONSTRAINT command_execution_results_status_valid CHECK (
        status IN ('passed', 'failed', 'timed_out', 'launch_failed', 'interrupted')
    ),
    ADD CONSTRAINT command_execution_results_exit_consistent CHECK (
        (status IN ('passed', 'failed') AND exit_code IS NOT NULL)
        OR (status IN ('timed_out', 'launch_failed', 'interrupted') AND exit_code IS NULL)
    ),
    ADD CONSTRAINT command_execution_results_recovery_consistent CHECK (
        (
            status = 'interrupted'
            AND recovery_id IS NOT NULL
            AND btrim(recovery_id) <> ''
            AND recovery_actor IS NOT NULL
            AND btrim(recovery_actor) <> ''
            AND recovery_reason IS NOT NULL
            AND btrim(recovery_reason) <> ''
            AND recovery_stale_before IS NOT NULL
        )
        OR (
            status <> 'interrupted'
            AND recovery_id IS NULL
            AND recovery_actor IS NULL
            AND recovery_reason IS NULL
            AND recovery_stale_before IS NULL
        )
    );

CREATE UNIQUE INDEX command_execution_results_recovery_id_unique
    ON command_execution_results (recovery_id)
    WHERE recovery_id IS NOT NULL;
