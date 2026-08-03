ALTER TABLE edit_packets
    DROP CONSTRAINT edit_packets_arrays_valid,
    ADD CONSTRAINT edit_packets_arrays_valid CHECK (
        jsonb_typeof(editable_files) = 'array'
        AND jsonb_typeof(read_only_files) = 'array'
        AND jsonb_typeof(test_files) = 'array'
        AND jsonb_typeof(forbidden_globs) = 'array'
        AND jsonb_typeof(allowed_items) = 'array'
        AND jsonb_typeof(required_ui_scenario_refs) = 'array'
        AND (
            (
                jsonb_array_length(editable_files) > 0
                AND jsonb_array_length(allowed_items) > 0
            )
            OR (
                jsonb_array_length(editable_files) = 0
                AND jsonb_array_length(allowed_items) = 0
                AND (
                    jsonb_array_length(read_only_files) > 0
                    OR jsonb_array_length(test_files) > 0
                )
            )
        )
    );

ALTER TABLE approval_grants
    DROP CONSTRAINT approval_grants_arrays_valid,
    ADD CONSTRAINT approval_grants_arrays_valid CHECK (
        jsonb_typeof(editable_files) = 'array'
        AND jsonb_typeof(read_only_files) = 'array'
        AND jsonb_typeof(test_files) = 'array'
        AND jsonb_typeof(allowed_actions) = 'array'
        AND jsonb_array_length(allowed_actions) > 0
        AND jsonb_typeof(allowed_test_command_refs) = 'array'
        AND jsonb_typeof(allowed_ui_scenarios) = 'array'
        AND jsonb_typeof(forbidden_globs) = 'array'
        AND jsonb_array_length(forbidden_globs) > 0
    );
