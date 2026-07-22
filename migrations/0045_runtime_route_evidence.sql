ALTER TABLE code_graph_scan_lineage
    DROP CONSTRAINT code_graph_scan_lineage_mode_valid,
    DROP CONSTRAINT code_graph_scan_lineage_base_consistent;

ALTER TABLE code_graph_scan_lineage
    ADD COLUMN runtime_evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD CONSTRAINT code_graph_scan_lineage_mode_valid CHECK (
        scan_mode IN ('full', 'incremental', 'runtime_enriched')
    ),
    ADD CONSTRAINT code_graph_scan_lineage_base_consistent CHECK (
        (scan_mode = 'full' AND base_code_graph_snapshot_id IS NULL)
        OR (scan_mode IN ('incremental', 'runtime_enriched')
            AND base_code_graph_snapshot_id IS NOT NULL)
    ),
    ADD CONSTRAINT code_graph_scan_lineage_runtime_evidence_valid CHECK (
        jsonb_typeof(runtime_evidence_refs) = 'array'
        AND (
            (scan_mode = 'runtime_enriched' AND jsonb_array_length(runtime_evidence_refs) > 0)
            OR (scan_mode <> 'runtime_enriched' AND jsonb_array_length(runtime_evidence_refs) = 0)
        )
    );

ALTER TABLE code_edges
    ADD COLUMN provenance text NOT NULL DEFAULT 'static',
    ADD COLUMN evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN static_edge_ref text,
    ADD CONSTRAINT code_edges_provenance_valid CHECK (
        provenance IN ('static', 'runtime', 'static_runtime')
    ),
    ADD CONSTRAINT code_edges_evidence_refs_valid CHECK (
        jsonb_typeof(evidence_refs) = 'array'
    ),
    ADD CONSTRAINT code_edges_runtime_provenance_consistent CHECK (
        (provenance = 'static' AND jsonb_array_length(evidence_refs) = 0
            AND static_edge_ref IS NULL)
        OR (provenance IN ('runtime', 'static_runtime')
            AND jsonb_array_length(evidence_refs) > 0)
    );

CREATE TABLE runtime_route_evidence (
    runtime_route_evidence_id text PRIMARY KEY,
    project_id text NOT NULL,
    repository_id text NOT NULL,
    repository_revision text NOT NULL,
    code_graph_snapshot_id text NOT NULL,
    browser_run_id text NOT NULL,
    captured_at timestamptz NOT NULL,
    source_evidence_refs jsonb NOT NULL,
    observation_count integer NOT NULL,
    resolved_count integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT runtime_route_evidence_fields_not_blank CHECK (
        btrim(runtime_route_evidence_id) <> ''
        AND btrim(repository_id) <> ''
        AND btrim(repository_revision) <> ''
        AND btrim(browser_run_id) <> ''
    ),
    CONSTRAINT runtime_route_evidence_refs_valid CHECK (
        jsonb_typeof(source_evidence_refs) = 'array'
        AND jsonb_array_length(source_evidence_refs) > 0
    ),
    CONSTRAINT runtime_route_evidence_counts_valid CHECK (
        observation_count > 0
        AND resolved_count >= 0
        AND resolved_count <= observation_count
    ),
    CONSTRAINT runtime_route_evidence_graph_fk FOREIGN KEY (
        code_graph_snapshot_id, project_id
    ) REFERENCES code_graph_snapshots(code_graph_snapshot_id, project_id),
    CONSTRAINT runtime_route_evidence_scope_unique UNIQUE (
        runtime_route_evidence_id, project_id
    )
);

CREATE TABLE runtime_route_observations (
    runtime_route_evidence_id text NOT NULL,
    project_id text NOT NULL,
    observation_id text NOT NULL,
    scenario_id text NOT NULL,
    event_kind text NOT NULL,
    method text NOT NULL,
    path text NOT NULL,
    source_action_id text,
    source_route_ref text,
    evidence_ref text NOT NULL,
    CONSTRAINT runtime_route_observations_fields_not_blank CHECK (
        btrim(observation_id) <> ''
        AND btrim(scenario_id) <> ''
        AND btrim(method) <> ''
        AND btrim(path) <> ''
        AND btrim(evidence_ref) <> ''
    ),
    CONSTRAINT runtime_route_observations_kind_valid CHECK (
        event_kind IN ('network_request', 'navigation', 'form_submission')
    ),
    CONSTRAINT runtime_route_observations_path_valid CHECK (left(path, 1) = '/'),
    CONSTRAINT runtime_route_observations_evidence_fk FOREIGN KEY (
        runtime_route_evidence_id, project_id
    ) REFERENCES runtime_route_evidence(runtime_route_evidence_id, project_id),
    CONSTRAINT runtime_route_observations_identity PRIMARY KEY (
        runtime_route_evidence_id, observation_id
    )
);

CREATE TABLE runtime_route_resolutions (
    runtime_route_evidence_id text NOT NULL,
    project_id text NOT NULL,
    observation_id text NOT NULL,
    status text NOT NULL,
    reason text NOT NULL,
    source_route_ref text,
    endpoint_ref text,
    candidate_endpoint_refs jsonb NOT NULL,
    CONSTRAINT runtime_route_resolutions_status_valid CHECK (
        status IN ('resolved', 'unresolved')
    ),
    CONSTRAINT runtime_route_resolutions_candidates_valid CHECK (
        jsonb_typeof(candidate_endpoint_refs) = 'array'
    ),
    CONSTRAINT runtime_route_resolutions_result_consistent CHECK (
        (status = 'resolved' AND endpoint_ref IS NOT NULL
            AND jsonb_array_length(candidate_endpoint_refs) = 1)
        OR (status = 'unresolved' AND endpoint_ref IS NULL)
    ),
    CONSTRAINT runtime_route_resolutions_observation_fk FOREIGN KEY (
        runtime_route_evidence_id, observation_id
    ) REFERENCES runtime_route_observations(runtime_route_evidence_id, observation_id),
    CONSTRAINT runtime_route_resolutions_identity PRIMARY KEY (
        runtime_route_evidence_id, observation_id
    )
);

CREATE INDEX runtime_route_evidence_graph_idx
    ON runtime_route_evidence (project_id, code_graph_snapshot_id, captured_at DESC);

CREATE INDEX runtime_route_observations_route_idx
    ON runtime_route_observations (project_id, method, path);
