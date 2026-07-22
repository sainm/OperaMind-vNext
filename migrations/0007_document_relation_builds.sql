ALTER TABLE profile_versions
    DROP CONSTRAINT profile_versions_type_valid;

ALTER TABLE profile_versions
    ADD CONSTRAINT profile_versions_type_valid CHECK (
        profile_type IN (
            'EmbeddingProfile',
            'DocumentConventionProfile',
            'DocumentRelationProfile',
            'CodeFrameworkProfile'
        )
    );

ALTER TABLE document_relations
    ADD CONSTRAINT document_relations_project_snapshot_identity_unique UNIQUE (
        project_id,
        document_snapshot_id,
        document_relation_id
    );

CREATE TABLE document_relation_builds (
    document_relation_build_id text PRIMARY KEY,
    project_id text NOT NULL,
    document_snapshot_id text NOT NULL,
    relation_profile_version_id text NOT NULL REFERENCES profile_versions(profile_version_id),
    status text NOT NULL,
    relation_count integer NOT NULL,
    unresolved_count integer NOT NULL,
    is_current boolean NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_relation_builds_identity_not_blank CHECK (
        btrim(document_relation_build_id) <> ''
    ),
    CONSTRAINT document_relation_builds_status_valid CHECK (
        status IN ('ready', 'stale')
    ),
    CONSTRAINT document_relation_builds_counts_valid CHECK (
        relation_count >= 0 AND unresolved_count >= 0
    ),
    CONSTRAINT document_relation_builds_current_consistent CHECK (
        NOT is_current OR status = 'ready'
    ),
    CONSTRAINT document_relation_builds_snapshot_fk FOREIGN KEY (
        project_id,
        document_snapshot_id
    ) REFERENCES document_snapshots(project_id, document_snapshot_id),
    CONSTRAINT document_relation_builds_scope_identity_unique UNIQUE (
        document_relation_build_id,
        project_id,
        document_snapshot_id
    )
);

CREATE UNIQUE INDEX document_relation_builds_current_snapshot_unique
    ON document_relation_builds (project_id, document_snapshot_id)
    WHERE is_current;

CREATE INDEX document_relation_builds_snapshot_status_idx
    ON document_relation_builds (
        project_id,
        document_snapshot_id,
        status,
        completed_at DESC
    );

CREATE TABLE document_relation_entries (
    document_relation_build_id text NOT NULL,
    project_id text NOT NULL,
    document_snapshot_id text NOT NULL,
    document_relation_id text NOT NULL,
    rule_id text NOT NULL,
    match_key_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_relation_entries_rule_not_blank CHECK (btrim(rule_id) <> ''),
    CONSTRAINT document_relation_entries_digest_sha256 CHECK (
        match_key_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT document_relation_entries_build_fk FOREIGN KEY (
        document_relation_build_id,
        project_id,
        document_snapshot_id
    ) REFERENCES document_relation_builds(
        document_relation_build_id,
        project_id,
        document_snapshot_id
    ),
    CONSTRAINT document_relation_entries_relation_fk FOREIGN KEY (
        project_id,
        document_snapshot_id,
        document_relation_id
    ) REFERENCES document_relations(
        project_id,
        document_snapshot_id,
        document_relation_id
    ),
    CONSTRAINT document_relation_entries_identity PRIMARY KEY (
        document_relation_build_id,
        document_relation_id,
        rule_id
    )
);

CREATE INDEX document_relation_entries_relation_idx
    ON document_relation_entries (
        project_id,
        document_snapshot_id,
        document_relation_id
    );

CREATE TABLE document_relation_unresolved (
    unresolved_relation_id text PRIMARY KEY,
    document_relation_build_id text NOT NULL,
    project_id text NOT NULL,
    document_snapshot_id text NOT NULL,
    rule_id text NOT NULL,
    source_node_id text NOT NULL,
    match_key_digest text,
    candidate_target_count integer NOT NULL,
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_relation_unresolved_identity_not_blank CHECK (
        btrim(unresolved_relation_id) <> '' AND btrim(rule_id) <> ''
    ),
    CONSTRAINT document_relation_unresolved_digest_sha256 CHECK (
        match_key_digest IS NULL OR match_key_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT document_relation_unresolved_candidate_count_valid CHECK (
        candidate_target_count >= 0
    ),
    CONSTRAINT document_relation_unresolved_reason_valid CHECK (
        reason IN ('missing_source_value', 'no_target', 'ambiguous_target', 'self_target')
    ),
    CONSTRAINT document_relation_unresolved_missing_consistent CHECK (
        (reason = 'missing_source_value' AND match_key_digest IS NULL)
        OR (reason <> 'missing_source_value' AND match_key_digest IS NOT NULL)
    ),
    CONSTRAINT document_relation_unresolved_build_fk FOREIGN KEY (
        document_relation_build_id,
        project_id,
        document_snapshot_id
    ) REFERENCES document_relation_builds(
        document_relation_build_id,
        project_id,
        document_snapshot_id
    ),
    CONSTRAINT document_relation_unresolved_source_fk FOREIGN KEY (
        project_id,
        document_snapshot_id,
        source_node_id
    ) REFERENCES document_nodes(
        project_id,
        document_snapshot_id,
        document_node_id
    ),
    CONSTRAINT document_relation_unresolved_source_unique UNIQUE (
        document_relation_build_id,
        rule_id,
        source_node_id
    )
);

ALTER TABLE search_index_builds
    ADD COLUMN document_relation_build_id text;

ALTER TABLE search_index_builds
    ADD CONSTRAINT search_index_builds_relation_build_fk FOREIGN KEY (
        document_relation_build_id,
        project_id,
        document_snapshot_id
    ) REFERENCES document_relation_builds(
        document_relation_build_id,
        project_id,
        document_snapshot_id
    );
