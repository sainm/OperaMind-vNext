CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE TABLE document_nodes (
    document_node_id text PRIMARY KEY,
    project_id text NOT NULL,
    document_snapshot_id text NOT NULL,
    document_version_id text NOT NULL,
    parent_node_id text,
    node_type text NOT NULL,
    ordinal integer NOT NULL,
    heading_path jsonb NOT NULL,
    business_keys jsonb NOT NULL,
    summary text NOT NULL,
    content text NOT NULL,
    source_refs jsonb NOT NULL,
    index_eligible boolean NOT NULL,
    content_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_nodes_identity_not_blank CHECK (
        btrim(document_node_id) <> ''
        AND btrim(summary) <> ''
        AND btrim(content) <> ''
    ),
    CONSTRAINT document_nodes_type_valid CHECK (node_type IN ('section', 'slice')),
    CONSTRAINT document_nodes_structure_valid CHECK (
        (
            node_type = 'section'
            AND parent_node_id IS NULL
            AND NOT index_eligible
        )
        OR (
            node_type = 'slice'
            AND parent_node_id IS NOT NULL
            AND index_eligible
        )
    ),
    CONSTRAINT document_nodes_ordinal_valid CHECK (ordinal >= 0),
    CONSTRAINT document_nodes_heading_path_array CHECK (
        jsonb_typeof(heading_path) = 'array'
        AND jsonb_array_length(heading_path) > 0
    ),
    CONSTRAINT document_nodes_business_keys_array CHECK (
        jsonb_typeof(business_keys) = 'array'
    ),
    CONSTRAINT document_nodes_source_refs_array CHECK (
        jsonb_typeof(source_refs) = 'array'
        AND jsonb_array_length(source_refs) > 0
    ),
    CONSTRAINT document_nodes_digest_sha256 CHECK (content_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT document_nodes_membership_fk FOREIGN KEY (
        project_id,
        document_snapshot_id,
        document_version_id
    ) REFERENCES snapshot_memberships(
        project_id,
        document_snapshot_id,
        document_version_id
    ),
    CONSTRAINT document_nodes_project_snapshot_identity_unique UNIQUE (
        project_id,
        document_snapshot_id,
        document_node_id
    ),
    CONSTRAINT document_nodes_parent_fk FOREIGN KEY (
        project_id,
        document_snapshot_id,
        parent_node_id
    ) REFERENCES document_nodes(
        project_id,
        document_snapshot_id,
        document_node_id
    )
);

CREATE INDEX document_nodes_snapshot_eligible_idx
    ON document_nodes (
        project_id,
        document_snapshot_id,
        index_eligible,
        document_node_id
    );

CREATE TABLE document_relations (
    document_relation_id text PRIMARY KEY,
    project_id text NOT NULL,
    document_snapshot_id text NOT NULL,
    source_node_id text NOT NULL,
    target_node_id text NOT NULL,
    relation_label text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_relations_identity_not_blank CHECK (
        btrim(document_relation_id) <> '' AND btrim(relation_label) <> ''
    ),
    CONSTRAINT document_relations_not_self CHECK (source_node_id <> target_node_id),
    CONSTRAINT document_relations_source_fk FOREIGN KEY (
        project_id,
        document_snapshot_id,
        source_node_id
    ) REFERENCES document_nodes(
        project_id,
        document_snapshot_id,
        document_node_id
    ),
    CONSTRAINT document_relations_target_fk FOREIGN KEY (
        project_id,
        document_snapshot_id,
        target_node_id
    ) REFERENCES document_nodes(
        project_id,
        document_snapshot_id,
        document_node_id
    ),
    CONSTRAINT document_relations_semantic_unique UNIQUE (
        project_id,
        document_snapshot_id,
        source_node_id,
        target_node_id,
        relation_label
    )
);

CREATE TABLE search_index_builds (
    search_index_build_id text PRIMARY KEY,
    project_id text NOT NULL,
    document_snapshot_id text NOT NULL,
    embedding_profile_version_id text NOT NULL REFERENCES profile_versions(profile_version_id),
    embedding_model text NOT NULL,
    dimensions integer NOT NULL,
    preprocessing_version text NOT NULL,
    ranking_policy_version text NOT NULL,
    status text NOT NULL,
    eligible_target_count integer NOT NULL DEFAULT 0,
    indexed_target_count integer NOT NULL DEFAULT 0,
    reused_vector_count integer NOT NULL DEFAULT 0,
    is_current boolean NOT NULL DEFAULT false,
    failure_reason text,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CONSTRAINT search_index_builds_identity_not_blank CHECK (
        btrim(search_index_build_id) <> ''
        AND btrim(embedding_model) <> ''
        AND btrim(preprocessing_version) <> ''
        AND btrim(ranking_policy_version) <> ''
    ),
    CONSTRAINT search_index_builds_dimensions_valid CHECK (
        dimensions BETWEEN 1 AND 16000
    ),
    CONSTRAINT search_index_builds_status_valid CHECK (
        status IN ('building', 'ready', 'failed', 'stale')
    ),
    CONSTRAINT search_index_builds_counts_valid CHECK (
        eligible_target_count >= 0
        AND indexed_target_count >= 0
        AND reused_vector_count >= 0
        AND indexed_target_count <= eligible_target_count
        AND reused_vector_count <= indexed_target_count
    ),
    CONSTRAINT search_index_builds_ready_consistent CHECK (
        status <> 'ready'
        OR (
            indexed_target_count = eligible_target_count
            AND failure_reason IS NULL
            AND completed_at IS NOT NULL
        )
    ),
    CONSTRAINT search_index_builds_failure_consistent CHECK (
        status <> 'failed' OR failure_reason IS NOT NULL
    ),
    CONSTRAINT search_index_builds_current_consistent CHECK (
        NOT is_current OR status = 'ready'
    ),
    CONSTRAINT search_index_builds_snapshot_fk FOREIGN KEY (
        project_id,
        document_snapshot_id
    ) REFERENCES document_snapshots(project_id, document_snapshot_id),
    CONSTRAINT search_index_builds_full_identity_unique UNIQUE (
        search_index_build_id,
        project_id,
        document_snapshot_id,
        embedding_profile_version_id,
        embedding_model,
        dimensions,
        preprocessing_version
    )
);

CREATE UNIQUE INDEX search_index_builds_current_snapshot_unique
    ON search_index_builds (project_id, document_snapshot_id)
    WHERE is_current;

CREATE INDEX search_index_builds_snapshot_status_idx
    ON search_index_builds (
        project_id,
        document_snapshot_id,
        status,
        started_at DESC
    );

CREATE TABLE document_search_vectors (
    vector_cache_id text PRIMARY KEY,
    input_digest text NOT NULL,
    embedding_model text NOT NULL,
    dimensions integer NOT NULL,
    preprocessing_version text NOT NULL,
    embedding public.vector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_search_vectors_identity_not_blank CHECK (
        btrim(vector_cache_id) <> ''
        AND btrim(embedding_model) <> ''
        AND btrim(preprocessing_version) <> ''
    ),
    CONSTRAINT document_search_vectors_digest_sha256 CHECK (
        input_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT document_search_vectors_dimensions_valid CHECK (
        dimensions BETWEEN 1 AND 16000
        AND public.vector_dims(embedding) = dimensions
    ),
    CONSTRAINT document_search_vectors_semantic_unique UNIQUE (
        input_digest,
        embedding_model,
        dimensions,
        preprocessing_version
    ),
    CONSTRAINT document_search_vectors_full_identity_unique UNIQUE (
        vector_cache_id,
        input_digest,
        embedding_model,
        dimensions,
        preprocessing_version
    )
);

CREATE TABLE search_index_entries (
    search_index_build_id text NOT NULL,
    project_id text NOT NULL,
    document_snapshot_id text NOT NULL,
    embedding_profile_version_id text NOT NULL,
    target_node_id text NOT NULL,
    input_digest text NOT NULL,
    vector_cache_id text NOT NULL,
    embedding_model text NOT NULL,
    dimensions integer NOT NULL,
    preprocessing_version text NOT NULL,
    keyword_text text NOT NULL,
    keyword_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', keyword_text)
    ) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT search_index_entries_keyword_not_blank CHECK (btrim(keyword_text) <> ''),
    CONSTRAINT search_index_entries_build_fk FOREIGN KEY (
        search_index_build_id,
        project_id,
        document_snapshot_id,
        embedding_profile_version_id,
        embedding_model,
        dimensions,
        preprocessing_version
    ) REFERENCES search_index_builds(
        search_index_build_id,
        project_id,
        document_snapshot_id,
        embedding_profile_version_id,
        embedding_model,
        dimensions,
        preprocessing_version
    ),
    CONSTRAINT search_index_entries_target_fk FOREIGN KEY (
        project_id,
        document_snapshot_id,
        target_node_id
    ) REFERENCES document_nodes(
        project_id,
        document_snapshot_id,
        document_node_id
    ),
    CONSTRAINT search_index_entries_vector_fk FOREIGN KEY (
        vector_cache_id,
        input_digest,
        embedding_model,
        dimensions,
        preprocessing_version
    ) REFERENCES document_search_vectors(
        vector_cache_id,
        input_digest,
        embedding_model,
        dimensions,
        preprocessing_version
    ),
    CONSTRAINT search_index_entries_identity PRIMARY KEY (
        search_index_build_id,
        target_node_id
    )
);

CREATE INDEX search_index_entries_keyword_idx
    ON search_index_entries USING gin (keyword_tsv);

CREATE INDEX search_index_entries_snapshot_idx
    ON search_index_entries (
        project_id,
        document_snapshot_id,
        embedding_profile_version_id,
        target_node_id
    );
