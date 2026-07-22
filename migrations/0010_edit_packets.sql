CREATE TABLE edit_packets (
    edit_packet_id text PRIMARY KEY,
    project_id text NOT NULL,
    analysis_case_id text NOT NULL,
    impact_report_id text NOT NULL,
    confirmation_id text NOT NULL,
    repository_id text NOT NULL,
    repository_revision_id text NOT NULL,
    base_repository_revision text NOT NULL,
    status text NOT NULL,
    editable_files jsonb NOT NULL,
    read_only_files jsonb NOT NULL,
    test_files jsonb NOT NULL,
    forbidden_globs jsonb NOT NULL,
    allowed_items jsonb NOT NULL,
    required_ui_scenario_refs jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT edit_packets_fields_not_blank CHECK (
        btrim(edit_packet_id) <> '' AND btrim(base_repository_revision) <> ''
    ),
    CONSTRAINT edit_packets_status_valid CHECK (
        status IN ('active', 'superseded')
    ),
    CONSTRAINT edit_packets_arrays_valid CHECK (
        jsonb_typeof(editable_files) = 'array'
        AND jsonb_array_length(editable_files) > 0
        AND jsonb_typeof(read_only_files) = 'array'
        AND jsonb_typeof(test_files) = 'array'
        AND jsonb_typeof(forbidden_globs) = 'array'
        AND jsonb_typeof(allowed_items) = 'array'
        AND jsonb_array_length(allowed_items) > 0
        AND jsonb_typeof(required_ui_scenario_refs) = 'array'
    ),
    CONSTRAINT edit_packets_case_fk FOREIGN KEY (
        analysis_case_id,
        project_id
    ) REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT edit_packets_report_fk FOREIGN KEY (
        impact_report_id,
        project_id
    ) REFERENCES impact_reports(impact_report_id, project_id),
    CONSTRAINT edit_packets_confirmation_fk FOREIGN KEY (
        confirmation_id,
        project_id
    ) REFERENCES impact_confirmations(confirmation_id, project_id),
    CONSTRAINT edit_packets_repository_fk FOREIGN KEY (
        repository_id,
        project_id
    ) REFERENCES repositories(repository_id, project_id),
    CONSTRAINT edit_packets_revision_fk FOREIGN KEY (
        repository_revision_id,
        repository_id
    ) REFERENCES repository_revisions(repository_revision_id, repository_id),
    CONSTRAINT edit_packets_scope_identity_unique UNIQUE (
        edit_packet_id,
        project_id
    )
);

CREATE UNIQUE INDEX edit_packets_active_case_unique
    ON edit_packets (project_id, analysis_case_id)
    WHERE status = 'active';

CREATE INDEX edit_packets_report_confirmation_idx
    ON edit_packets (project_id, impact_report_id, confirmation_id);
