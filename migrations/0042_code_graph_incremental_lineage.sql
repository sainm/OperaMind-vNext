CREATE TABLE code_graph_scan_lineage (
    code_graph_snapshot_id text PRIMARY KEY,
    project_id text NOT NULL,
    scan_mode text NOT NULL,
    base_code_graph_snapshot_id text,
    changed_paths jsonb NOT NULL,
    affected_paths jsonb NOT NULL,
    scanned_file_count integer NOT NULL,
    reused_file_count integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT code_graph_scan_lineage_mode_valid CHECK (
        scan_mode IN ('full', 'incremental')
    ),
    CONSTRAINT code_graph_scan_lineage_paths_valid CHECK (
        jsonb_typeof(changed_paths) = 'array'
        AND jsonb_typeof(affected_paths) = 'array'
    ),
    CONSTRAINT code_graph_scan_lineage_counts_valid CHECK (
        scanned_file_count >= 0 AND reused_file_count >= 0
    ),
    CONSTRAINT code_graph_scan_lineage_base_consistent CHECK (
        (scan_mode = 'full' AND base_code_graph_snapshot_id IS NULL)
        OR (scan_mode = 'incremental' AND base_code_graph_snapshot_id IS NOT NULL)
    ),
    CONSTRAINT code_graph_scan_lineage_snapshot_fk FOREIGN KEY (
        code_graph_snapshot_id, project_id
    ) REFERENCES code_graph_snapshots(code_graph_snapshot_id, project_id),
    CONSTRAINT code_graph_scan_lineage_base_fk FOREIGN KEY (
        base_code_graph_snapshot_id, project_id
    ) REFERENCES code_graph_snapshots(code_graph_snapshot_id, project_id)
);

INSERT INTO code_graph_scan_lineage (
    code_graph_snapshot_id,
    project_id,
    scan_mode,
    base_code_graph_snapshot_id,
    changed_paths,
    affected_paths,
    scanned_file_count,
    reused_file_count,
    created_at
)
SELECT
    snapshot.code_graph_snapshot_id,
    snapshot.project_id,
    'full',
    NULL,
    '[]'::jsonb,
    COALESCE(paths.value, '[]'::jsonb),
    snapshot.file_count,
    0,
    snapshot.created_at
FROM code_graph_snapshots AS snapshot
LEFT JOIN LATERAL (
    SELECT jsonb_agg(file.path ORDER BY file.path) AS value
    FROM code_files AS file
    WHERE file.code_graph_snapshot_id = snapshot.code_graph_snapshot_id
      AND file.project_id = snapshot.project_id
) AS paths ON true;

CREATE INDEX code_graph_scan_lineage_base_idx
    ON code_graph_scan_lineage (
        project_id, base_code_graph_snapshot_id, created_at DESC
    );
