ALTER TABLE repositories
    ADD CONSTRAINT repositories_project_identity_unique UNIQUE (
        repository_id,
        project_id
    );

ALTER TABLE repository_revisions
    ADD CONSTRAINT repository_revisions_repository_identity_unique UNIQUE (
        repository_revision_id,
        repository_id
    );

CREATE TABLE code_graph_snapshots (
    code_graph_snapshot_id text PRIMARY KEY,
    project_id text NOT NULL,
    repository_id text NOT NULL,
    repository_revision_id text NOT NULL,
    status text NOT NULL,
    scan_roots jsonb NOT NULL,
    file_count integer NOT NULL,
    symbol_count integer NOT NULL,
    edge_count integer NOT NULL,
    unresolved_edge_count integer NOT NULL,
    is_current boolean NOT NULL,
    failure_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT code_graph_snapshots_identity_not_blank CHECK (
        btrim(code_graph_snapshot_id) <> ''
    ),
    CONSTRAINT code_graph_snapshots_status_valid CHECK (
        status IN ('complete', 'truncated', 'failed', 'stale')
    ),
    CONSTRAINT code_graph_snapshots_scan_roots_valid CHECK (
        jsonb_typeof(scan_roots) = 'array' AND jsonb_array_length(scan_roots) > 0
    ),
    CONSTRAINT code_graph_snapshots_counts_valid CHECK (
        file_count >= 0
        AND symbol_count >= 0
        AND edge_count >= 0
        AND unresolved_edge_count >= 0
        AND unresolved_edge_count <= edge_count
    ),
    CONSTRAINT code_graph_snapshots_current_consistent CHECK (
        NOT is_current OR status IN ('complete', 'truncated')
    ),
    CONSTRAINT code_graph_snapshots_failure_consistent CHECK (
        (status = 'failed' AND failure_reason IS NOT NULL AND btrim(failure_reason) <> '')
        OR (status <> 'failed' AND failure_reason IS NULL)
    ),
    CONSTRAINT code_graph_snapshots_repository_fk FOREIGN KEY (
        repository_id,
        project_id
    ) REFERENCES repositories(repository_id, project_id),
    CONSTRAINT code_graph_snapshots_revision_fk FOREIGN KEY (
        repository_revision_id,
        repository_id
    ) REFERENCES repository_revisions(repository_revision_id, repository_id),
    CONSTRAINT code_graph_snapshots_scope_identity_unique UNIQUE (
        code_graph_snapshot_id,
        project_id
    )
);

CREATE UNIQUE INDEX code_graph_snapshots_current_repository_unique
    ON code_graph_snapshots (project_id, repository_id)
    WHERE is_current;

CREATE INDEX code_graph_snapshots_revision_status_idx
    ON code_graph_snapshots (
        project_id,
        repository_id,
        repository_revision_id,
        status,
        completed_at DESC
    );

CREATE TABLE code_graph_snapshot_profiles (
    code_graph_snapshot_id text NOT NULL,
    project_id text NOT NULL,
    profile_version_id text NOT NULL REFERENCES profile_versions(profile_version_id),
    profile_ref text NOT NULL,
    CONSTRAINT code_graph_snapshot_profiles_ref_not_blank CHECK (
        btrim(profile_ref) <> ''
    ),
    CONSTRAINT code_graph_snapshot_profiles_snapshot_fk FOREIGN KEY (
        code_graph_snapshot_id,
        project_id
    ) REFERENCES code_graph_snapshots(code_graph_snapshot_id, project_id),
    CONSTRAINT code_graph_snapshot_profiles_identity PRIMARY KEY (
        code_graph_snapshot_id,
        profile_version_id
    ),
    CONSTRAINT code_graph_snapshot_profiles_ref_unique UNIQUE (
        code_graph_snapshot_id,
        profile_ref
    )
);

CREATE TABLE code_files (
    code_graph_snapshot_id text NOT NULL,
    project_id text NOT NULL,
    code_file_id text NOT NULL,
    path text NOT NULL,
    language text NOT NULL,
    role text NOT NULL,
    content_hash text NOT NULL,
    CONSTRAINT code_files_fields_not_blank CHECK (
        btrim(code_file_id) <> ''
        AND btrim(path) <> ''
        AND btrim(language) <> ''
        AND btrim(content_hash) <> ''
    ),
    CONSTRAINT code_files_role_valid CHECK (
        role IN (
            'production',
            'test',
            'config',
            'migration',
            'contract',
            'script',
            'unknown'
        )
    ),
    CONSTRAINT code_files_snapshot_fk FOREIGN KEY (
        code_graph_snapshot_id,
        project_id
    ) REFERENCES code_graph_snapshots(code_graph_snapshot_id, project_id),
    CONSTRAINT code_files_identity PRIMARY KEY (
        code_graph_snapshot_id,
        code_file_id
    ),
    CONSTRAINT code_files_path_unique UNIQUE (
        code_graph_snapshot_id,
        path
    ),
    CONSTRAINT code_files_scope_identity_unique UNIQUE (
        code_graph_snapshot_id,
        project_id,
        code_file_id
    )
);

CREATE INDEX code_files_path_idx
    ON code_files (project_id, path);

CREATE TABLE code_symbols (
    code_graph_snapshot_id text NOT NULL,
    project_id text NOT NULL,
    code_symbol_id text NOT NULL,
    code_file_id text NOT NULL,
    symbol_type text NOT NULL,
    name text NOT NULL,
    signature text NOT NULL,
    start_line integer NOT NULL,
    end_line integer NOT NULL,
    CONSTRAINT code_symbols_fields_not_blank CHECK (
        btrim(code_symbol_id) <> ''
        AND btrim(symbol_type) <> ''
        AND btrim(name) <> ''
        AND btrim(signature) <> ''
    ),
    CONSTRAINT code_symbols_lines_valid CHECK (
        start_line >= 1 AND end_line >= start_line
    ),
    CONSTRAINT code_symbols_file_fk FOREIGN KEY (
        code_graph_snapshot_id,
        project_id,
        code_file_id
    ) REFERENCES code_files(code_graph_snapshot_id, project_id, code_file_id),
    CONSTRAINT code_symbols_identity PRIMARY KEY (
        code_graph_snapshot_id,
        code_symbol_id
    ),
    CONSTRAINT code_symbols_scope_identity_unique UNIQUE (
        code_graph_snapshot_id,
        project_id,
        code_symbol_id
    )
);

CREATE INDEX code_symbols_name_signature_idx
    ON code_symbols (project_id, name, signature);

CREATE TABLE code_edges (
    code_graph_snapshot_id text NOT NULL,
    project_id text NOT NULL,
    code_edge_id text NOT NULL,
    edge_type text NOT NULL,
    from_ref text NOT NULL,
    to_ref text NOT NULL,
    resolution_status text NOT NULL,
    confidence text NOT NULL,
    extractor text NOT NULL,
    profile_version_ref text NOT NULL,
    source_path text NOT NULL,
    source_start_line integer NOT NULL,
    source_end_line integer NOT NULL,
    CONSTRAINT code_edges_fields_not_blank CHECK (
        btrim(code_edge_id) <> ''
        AND btrim(from_ref) <> ''
        AND btrim(to_ref) <> ''
        AND btrim(extractor) <> ''
        AND btrim(profile_version_ref) <> ''
        AND btrim(source_path) <> ''
    ),
    CONSTRAINT code_edges_type_valid CHECK (
        edge_type IN (
            'contains',
            'imports',
            'calls',
            'implements',
            'exposes',
            'reads',
            'writes',
            'maps_to',
            'tests',
            'navigates_to'
        )
    ),
    CONSTRAINT code_edges_resolution_valid CHECK (
        resolution_status IN ('resolved', 'unresolved', 'external')
    ),
    CONSTRAINT code_edges_confidence_valid CHECK (
        confidence IN ('high', 'medium', 'low')
    ),
    CONSTRAINT code_edges_lines_valid CHECK (
        source_start_line >= 1 AND source_end_line >= source_start_line
    ),
    CONSTRAINT code_edges_snapshot_fk FOREIGN KEY (
        code_graph_snapshot_id,
        project_id
    ) REFERENCES code_graph_snapshots(code_graph_snapshot_id, project_id),
    CONSTRAINT code_edges_identity PRIMARY KEY (
        code_graph_snapshot_id,
        code_edge_id
    ),
    CONSTRAINT code_edges_scope_identity_unique UNIQUE (
        code_graph_snapshot_id,
        project_id,
        code_edge_id
    )
);

CREATE INDEX code_edges_forward_idx
    ON code_edges (project_id, code_graph_snapshot_id, from_ref, edge_type);

CREATE INDEX code_edges_reverse_idx
    ON code_edges (project_id, code_graph_snapshot_id, to_ref, edge_type);

CREATE TABLE code_test_bindings (
    code_test_binding_id text PRIMARY KEY,
    code_graph_snapshot_id text NOT NULL,
    project_id text NOT NULL,
    production_file_id text NOT NULL,
    test_file_id text NOT NULL,
    source_edge_id text NOT NULL,
    confidence text NOT NULL,
    extractor text NOT NULL,
    profile_version_ref text NOT NULL,
    CONSTRAINT code_test_bindings_fields_not_blank CHECK (
        btrim(code_test_binding_id) <> ''
        AND btrim(extractor) <> ''
        AND btrim(profile_version_ref) <> ''
    ),
    CONSTRAINT code_test_bindings_distinct_files CHECK (
        production_file_id <> test_file_id
    ),
    CONSTRAINT code_test_bindings_confidence_valid CHECK (
        confidence IN ('high', 'medium', 'low')
    ),
    CONSTRAINT code_test_bindings_production_file_fk FOREIGN KEY (
        code_graph_snapshot_id,
        project_id,
        production_file_id
    ) REFERENCES code_files(code_graph_snapshot_id, project_id, code_file_id),
    CONSTRAINT code_test_bindings_test_file_fk FOREIGN KEY (
        code_graph_snapshot_id,
        project_id,
        test_file_id
    ) REFERENCES code_files(code_graph_snapshot_id, project_id, code_file_id),
    CONSTRAINT code_test_bindings_source_edge_fk FOREIGN KEY (
        code_graph_snapshot_id,
        project_id,
        source_edge_id
    ) REFERENCES code_edges(code_graph_snapshot_id, project_id, code_edge_id),
    CONSTRAINT code_test_bindings_pair_unique UNIQUE (
        code_graph_snapshot_id,
        production_file_id,
        test_file_id
    )
);

CREATE INDEX code_test_bindings_production_idx
    ON code_test_bindings (project_id, code_graph_snapshot_id, production_file_id);
