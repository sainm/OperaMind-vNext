CREATE TABLE impact_reports (
    impact_report_id text PRIMARY KEY,
    project_id text NOT NULL,
    analysis_case_id text NOT NULL,
    document_snapshot_id text NOT NULL,
    context_package_id text NOT NULL,
    code_graph_snapshot_id text NOT NULL,
    repository_id text NOT NULL,
    repository_revision_id text NOT NULL,
    repository_revision text NOT NULL,
    analysis_policy_version text NOT NULL,
    status text NOT NULL,
    summary text NOT NULL,
    blocking_unknowns jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    confirmed_at timestamptz,
    CONSTRAINT impact_reports_fields_not_blank CHECK (
        btrim(impact_report_id) <> ''
        AND btrim(context_package_id) <> ''
        AND btrim(repository_revision) <> ''
        AND btrim(analysis_policy_version) <> ''
        AND btrim(summary) <> ''
    ),
    CONSTRAINT impact_reports_status_valid CHECK (
        status IN ('awaiting_confirmation', 'confirmed', 'superseded', 'blocked')
    ),
    CONSTRAINT impact_reports_unknowns_array CHECK (
        jsonb_typeof(blocking_unknowns) = 'array'
    ),
    CONSTRAINT impact_reports_blocked_consistent CHECK (
        (status = 'blocked' AND jsonb_array_length(blocking_unknowns) > 0)
        OR status <> 'blocked'
    ),
    CONSTRAINT impact_reports_confirmation_consistent CHECK (
        (status = 'confirmed' AND confirmed_at IS NOT NULL)
        OR (status IN ('awaiting_confirmation', 'blocked') AND confirmed_at IS NULL)
        OR status = 'superseded'
    ),
    CONSTRAINT impact_reports_case_fk FOREIGN KEY (
        analysis_case_id,
        project_id
    ) REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT impact_reports_snapshot_fk FOREIGN KEY (
        project_id,
        document_snapshot_id
    ) REFERENCES document_snapshots(project_id, document_snapshot_id),
    CONSTRAINT impact_reports_graph_fk FOREIGN KEY (
        code_graph_snapshot_id,
        project_id
    ) REFERENCES code_graph_snapshots(code_graph_snapshot_id, project_id),
    CONSTRAINT impact_reports_repository_fk FOREIGN KEY (
        repository_id,
        project_id
    ) REFERENCES repositories(repository_id, project_id),
    CONSTRAINT impact_reports_revision_fk FOREIGN KEY (
        repository_revision_id,
        repository_id
    ) REFERENCES repository_revisions(repository_revision_id, repository_id),
    CONSTRAINT impact_reports_scope_identity_unique UNIQUE (
        impact_report_id,
        project_id
    )
);

CREATE UNIQUE INDEX impact_reports_current_case_unique
    ON impact_reports (project_id, analysis_case_id)
    WHERE status IN ('awaiting_confirmation', 'confirmed', 'blocked');

CREATE TABLE impact_items (
    impact_report_id text NOT NULL,
    project_id text NOT NULL,
    impact_item_id text NOT NULL,
    structured_change_refs jsonb NOT NULL,
    target_path text NOT NULL,
    target_symbols jsonb NOT NULL,
    impact_level text NOT NULL,
    impact_score double precision,
    recommended_action text NOT NULL,
    rationale text,
    evidence_refs jsonb NOT NULL,
    graph_path_refs jsonb NOT NULL,
    test_file_refs jsonb NOT NULL,
    requires_confirmation boolean NOT NULL,
    unknowns jsonb NOT NULL,
    CONSTRAINT impact_items_fields_not_blank CHECK (
        btrim(impact_item_id) <> '' AND btrim(target_path) <> ''
    ),
    CONSTRAINT impact_items_level_valid CHECK (
        impact_level IN ('high', 'medium', 'low', 'unknown')
    ),
    CONSTRAINT impact_items_score_valid CHECK (
        impact_score IS NULL OR (impact_score >= 0 AND impact_score <= 1)
    ),
    CONSTRAINT impact_items_action_valid CHECK (
        recommended_action IN ('modify', 'add', 'delete', 'review_only', 'no_change')
    ),
    CONSTRAINT impact_items_json_arrays CHECK (
        jsonb_typeof(structured_change_refs) = 'array'
        AND jsonb_array_length(structured_change_refs) > 0
        AND jsonb_typeof(target_symbols) = 'array'
        AND jsonb_typeof(evidence_refs) = 'array'
        AND jsonb_typeof(graph_path_refs) = 'array'
        AND jsonb_typeof(test_file_refs) = 'array'
        AND jsonb_typeof(unknowns) = 'array'
    ),
    CONSTRAINT impact_items_report_fk FOREIGN KEY (
        impact_report_id,
        project_id
    ) REFERENCES impact_reports(impact_report_id, project_id),
    CONSTRAINT impact_items_identity PRIMARY KEY (
        impact_report_id,
        impact_item_id
    ),
    CONSTRAINT impact_items_path_unique UNIQUE (
        impact_report_id,
        target_path
    ),
    CONSTRAINT impact_items_scope_identity_unique UNIQUE (
        impact_report_id,
        project_id,
        impact_item_id
    )
);

CREATE TABLE impact_confirmations (
    confirmation_id text PRIMARY KEY,
    project_id text NOT NULL,
    analysis_case_id text NOT NULL,
    impact_report_id text NOT NULL,
    confirmed_by text NOT NULL,
    approved_item_ids jsonb NOT NULL,
    rejected_item_ids jsonb NOT NULL,
    user_note text,
    confirmed_at timestamptz NOT NULL,
    CONSTRAINT impact_confirmations_fields_not_blank CHECK (
        btrim(confirmation_id) <> '' AND btrim(confirmed_by) <> ''
    ),
    CONSTRAINT impact_confirmations_item_arrays CHECK (
        jsonb_typeof(approved_item_ids) = 'array'
        AND jsonb_typeof(rejected_item_ids) = 'array'
    ),
    CONSTRAINT impact_confirmations_report_fk FOREIGN KEY (
        impact_report_id,
        project_id
    ) REFERENCES impact_reports(impact_report_id, project_id),
    CONSTRAINT impact_confirmations_case_fk FOREIGN KEY (
        analysis_case_id,
        project_id
    ) REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT impact_confirmations_report_unique UNIQUE (impact_report_id),
    CONSTRAINT impact_confirmations_scope_identity_unique UNIQUE (
        confirmation_id,
        project_id
    )
);

CREATE INDEX impact_items_target_path_idx
    ON impact_items (project_id, target_path);

CREATE INDEX impact_confirmations_case_idx
    ON impact_confirmations (project_id, analysis_case_id, confirmed_at DESC);
