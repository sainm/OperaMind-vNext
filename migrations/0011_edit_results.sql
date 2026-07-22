CREATE TABLE edit_results (
    edit_result_id text PRIMARY KEY,
    edit_packet_id text NOT NULL,
    project_id text NOT NULL,
    analysis_case_id text NOT NULL,
    validation_mode text NOT NULL,
    status text NOT NULL,
    base_repository_revision text NOT NULL,
    result_repository_revision text,
    path_changes jsonb NOT NULL,
    changed_paths jsonb NOT NULL,
    out_of_scope_files jsonb NOT NULL,
    test_result_refs jsonb NOT NULL,
    tests_passed boolean,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT edit_results_fields_not_blank CHECK (
        btrim(edit_result_id) <> '' AND btrim(base_repository_revision) <> ''
    ),
    CONSTRAINT edit_results_mode_valid CHECK (
        validation_mode IN ('working', 'committed')
    ),
    CONSTRAINT edit_results_status_valid CHECK (
        status IN ('in_scope', 'out_of_scope', 'no_changes')
    ),
    CONSTRAINT edit_results_revision_consistent CHECK (
        (validation_mode = 'working' AND result_repository_revision IS NULL)
        OR (
            validation_mode = 'committed'
            AND result_repository_revision IS NOT NULL
            AND btrim(result_repository_revision) <> ''
        )
    ),
    CONSTRAINT edit_results_tests_consistent CHECK (
        (validation_mode = 'working' AND tests_passed IS NULL)
        OR (validation_mode = 'committed' AND tests_passed IS NOT NULL)
    ),
    CONSTRAINT edit_results_arrays_valid CHECK (
        jsonb_typeof(path_changes) = 'array'
        AND jsonb_typeof(changed_paths) = 'array'
        AND jsonb_typeof(out_of_scope_files) = 'array'
        AND jsonb_typeof(test_result_refs) = 'array'
    ),
    CONSTRAINT edit_results_scope_consistent CHECK (
        (status = 'out_of_scope' AND jsonb_array_length(out_of_scope_files) > 0)
        OR (status <> 'out_of_scope' AND jsonb_array_length(out_of_scope_files) = 0)
    ),
    CONSTRAINT edit_results_packet_fk FOREIGN KEY (
        edit_packet_id,
        project_id
    ) REFERENCES edit_packets(edit_packet_id, project_id),
    CONSTRAINT edit_results_case_fk FOREIGN KEY (
        analysis_case_id,
        project_id
    ) REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT edit_results_scope_identity_unique UNIQUE (
        edit_result_id,
        project_id
    )
);

CREATE INDEX edit_results_packet_recorded_idx
    ON edit_results (project_id, edit_packet_id, recorded_at DESC);
