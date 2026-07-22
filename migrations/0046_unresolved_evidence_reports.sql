CREATE TABLE unresolved_evidence_reports (
    unresolved_evidence_report_id text PRIMARY KEY,
    project_id text NOT NULL,
    repository_id text NOT NULL,
    repository_revision text NOT NULL,
    code_graph_snapshot_id text NOT NULL,
    predecessor_report_id text,
    report_status text NOT NULL,
    trigger_type text NOT NULL,
    trigger_evidence_refs jsonb NOT NULL,
    open_count integer NOT NULL,
    closed_count integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT unresolved_evidence_reports_fields_not_blank CHECK (
        btrim(unresolved_evidence_report_id) <> ''
        AND btrim(repository_id) <> ''
        AND btrim(repository_revision) <> ''
    ),
    CONSTRAINT unresolved_evidence_reports_status_valid CHECK (
        report_status IN ('clear', 'needs_evidence')
    ),
    CONSTRAINT unresolved_evidence_reports_trigger_valid CHECK (
        trigger_type IN ('static_graph', 'runtime_evidence')
        AND jsonb_typeof(trigger_evidence_refs) = 'array'
        AND jsonb_array_length(trigger_evidence_refs) > 0
    ),
    CONSTRAINT unresolved_evidence_reports_counts_valid CHECK (
        open_count >= 0
        AND closed_count >= 0
        AND (
            (report_status = 'clear' AND open_count = 0)
            OR (report_status = 'needs_evidence' AND open_count > 0)
        )
    ),
    CONSTRAINT unresolved_evidence_reports_graph_fk FOREIGN KEY (
        code_graph_snapshot_id, project_id
    ) REFERENCES code_graph_snapshots(code_graph_snapshot_id, project_id),
    CONSTRAINT unresolved_evidence_reports_scope_unique UNIQUE (
        unresolved_evidence_report_id, project_id
    ),
    CONSTRAINT unresolved_evidence_reports_graph_unique UNIQUE (
        code_graph_snapshot_id, project_id
    ),
    CONSTRAINT unresolved_evidence_reports_predecessor_fk FOREIGN KEY (
        predecessor_report_id, project_id
    ) REFERENCES unresolved_evidence_reports(unresolved_evidence_report_id, project_id)
);

CREATE TABLE unresolved_evidence_items (
    unresolved_evidence_report_id text NOT NULL,
    project_id text NOT NULL,
    item_id text NOT NULL,
    finding_key text NOT NULL,
    edge_ref text NOT NULL,
    status text NOT NULL,
    category text NOT NULL,
    reason text NOT NULL,
    edge_type text NOT NULL,
    source_ref text NOT NULL,
    unresolved_target_ref text NOT NULL,
    source_path text NOT NULL,
    source_start_line integer NOT NULL,
    source_end_line integer NOT NULL,
    candidate_targets jsonb NOT NULL,
    missing_evidence jsonb NOT NULL,
    resolution_suggestions jsonb NOT NULL,
    provenance text NOT NULL,
    evidence_refs jsonb NOT NULL,
    resolved_target_ref text,
    resolved_edge_ref text,
    proof_kind text,
    closure_evidence_refs jsonb,
    CONSTRAINT unresolved_evidence_items_fields_not_blank CHECK (
        btrim(item_id) <> ''
        AND btrim(finding_key) <> ''
        AND btrim(edge_ref) <> ''
        AND btrim(edge_type) <> ''
        AND btrim(source_ref) <> ''
        AND btrim(unresolved_target_ref) <> ''
        AND btrim(source_path) <> ''
    ),
    CONSTRAINT unresolved_evidence_items_status_valid CHECK (
        status IN ('open', 'closed')
    ),
    CONSTRAINT unresolved_evidence_items_category_valid CHECK (
        category IN (
            'call_target', 'endpoint_route', 'data_table', 'entity_mapping',
            'config_key', 'navigation_target', 'generic_relation'
        )
    ),
    CONSTRAINT unresolved_evidence_items_reason_valid CHECK (
        reason IN (
            'target_definition_missing', 'target_ambiguous',
            'runtime_observation_missing', 'external_reference_unverified',
            'dynamic_reference', 'unresolved_reference', 'resolved_unique'
        )
    ),
    CONSTRAINT unresolved_evidence_items_lines_valid CHECK (
        source_start_line >= 1 AND source_end_line >= source_start_line
    ),
    CONSTRAINT unresolved_evidence_items_arrays_valid CHECK (
        jsonb_typeof(candidate_targets) = 'array'
        AND jsonb_typeof(missing_evidence) = 'array'
        AND jsonb_typeof(resolution_suggestions) = 'array'
        AND jsonb_array_length(resolution_suggestions) > 0
        AND jsonb_typeof(evidence_refs) = 'array'
    ),
    CONSTRAINT unresolved_evidence_items_provenance_valid CHECK (
        provenance IN ('static', 'runtime', 'static_runtime')
    ),
    CONSTRAINT unresolved_evidence_items_closure_consistent CHECK (
        (
            status = 'open'
            AND reason <> 'resolved_unique'
            AND resolved_target_ref IS NULL
            AND resolved_edge_ref IS NULL
            AND proof_kind IS NULL
            AND closure_evidence_refs IS NULL
        )
        OR (
            status = 'closed'
            AND reason = 'resolved_unique'
            AND resolved_target_ref IS NOT NULL
            AND resolved_edge_ref IS NOT NULL
            AND proof_kind IN ('static_unique', 'runtime_unique', 'static_runtime_unique')
            AND jsonb_typeof(closure_evidence_refs) = 'array'
            AND jsonb_array_length(closure_evidence_refs) > 0
            AND jsonb_array_length(missing_evidence) = 0
            AND jsonb_array_length(candidate_targets) = 1
        )
    ),
    CONSTRAINT unresolved_evidence_items_report_fk FOREIGN KEY (
        unresolved_evidence_report_id, project_id
    ) REFERENCES unresolved_evidence_reports(unresolved_evidence_report_id, project_id),
    CONSTRAINT unresolved_evidence_items_identity PRIMARY KEY (
        unresolved_evidence_report_id, item_id
    ),
    CONSTRAINT unresolved_evidence_items_finding_unique UNIQUE (
        unresolved_evidence_report_id, finding_key, status
    )
);

CREATE INDEX unresolved_evidence_reports_project_history_idx
    ON unresolved_evidence_reports (project_id, repository_id, created_at DESC);

CREATE INDEX unresolved_evidence_items_open_reason_idx
    ON unresolved_evidence_items (project_id, status, reason, category);
