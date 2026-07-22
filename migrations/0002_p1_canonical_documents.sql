CREATE TABLE profile_versions (
    profile_version_id text PRIMARY KEY,
    profile_type text NOT NULL,
    profile_id text NOT NULL,
    semantic_version text NOT NULL,
    payload jsonb NOT NULL,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT profile_versions_type_valid CHECK (
        profile_type IN (
            'EmbeddingProfile',
            'DocumentConventionProfile',
            'CodeFrameworkProfile'
        )
    ),
    CONSTRAINT profile_versions_identity_not_blank CHECK (
        btrim(profile_id) <> '' AND btrim(semantic_version) <> ''
    ),
    CONSTRAINT profile_versions_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT profile_versions_digest_sha256 CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT profile_versions_envelope_matches_payload CHECK (
        payload ->> 'profile_type' = profile_type
        AND payload ->> 'profile_id' = profile_id
        AND payload ->> 'profile_version' = semantic_version
    ),
    CONSTRAINT profile_versions_semantic_identity_unique UNIQUE (
        profile_type,
        profile_id,
        semantic_version
    )
);

CREATE TABLE project_profile_bindings (
    project_id text NOT NULL REFERENCES projects(project_id),
    binding_key text NOT NULL,
    active_profile_version_id text NOT NULL REFERENCES profile_versions(profile_version_id),
    activated_by text NOT NULL,
    activated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT project_profile_bindings_key_not_blank CHECK (btrim(binding_key) <> ''),
    CONSTRAINT project_profile_bindings_actor_not_blank CHECK (btrim(activated_by) <> ''),
    CONSTRAINT project_profile_bindings_identity PRIMARY KEY (project_id, binding_key)
);

CREATE TABLE profile_activation_events (
    activation_event_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    binding_key text NOT NULL,
    previous_profile_version_id text REFERENCES profile_versions(profile_version_id),
    activated_profile_version_id text NOT NULL REFERENCES profile_versions(profile_version_id),
    activated_by text NOT NULL,
    reason text NOT NULL,
    activated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT profile_activation_events_key_not_blank CHECK (btrim(binding_key) <> ''),
    CONSTRAINT profile_activation_events_actor_not_blank CHECK (btrim(activated_by) <> ''),
    CONSTRAINT profile_activation_events_reason_not_blank CHECK (btrim(reason) <> '')
);

CREATE INDEX profile_activation_events_project_binding_idx
    ON profile_activation_events (project_id, binding_key, activated_at DESC);

CREATE TABLE documents (
    document_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    logical_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT documents_name_not_blank CHECK (btrim(logical_name) <> ''),
    CONSTRAINT documents_project_name_unique UNIQUE (project_id, logical_name),
    CONSTRAINT documents_project_identity_unique UNIQUE (project_id, document_id)
);

CREATE TABLE document_versions (
    document_version_id text PRIMARY KEY,
    project_id text NOT NULL,
    document_id text NOT NULL,
    source_ref text NOT NULL,
    content_digest text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_versions_source_not_blank CHECK (btrim(source_ref) <> ''),
    CONSTRAINT document_versions_digest_sha256 CHECK (content_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT document_versions_project_document_fk FOREIGN KEY (project_id, document_id)
        REFERENCES documents(project_id, document_id),
    CONSTRAINT document_versions_document_digest_unique UNIQUE (document_id, content_digest),
    CONSTRAINT document_versions_project_identity_unique UNIQUE (
        project_id,
        document_version_id
    )
);

CREATE TABLE document_snapshots (
    document_snapshot_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    committed_at timestamptz,
    CONSTRAINT document_snapshots_status_valid CHECK (
        status IN ('draft', 'needs_review', 'committed', 'rejected')
    ),
    CONSTRAINT document_snapshots_commit_consistent CHECK (
        status <> 'committed' OR committed_at IS NOT NULL
    ),
    CONSTRAINT document_snapshots_project_identity_unique UNIQUE (
        project_id,
        document_snapshot_id
    )
);

CREATE TABLE snapshot_memberships (
    project_id text NOT NULL,
    document_snapshot_id text NOT NULL,
    document_version_id text NOT NULL,
    profile_version_id text NOT NULL REFERENCES profile_versions(profile_version_id),
    selected_variant_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT snapshot_memberships_variant_not_blank CHECK (
        btrim(selected_variant_id) <> ''
    ),
    CONSTRAINT snapshot_memberships_snapshot_fk FOREIGN KEY (
        project_id,
        document_snapshot_id
    ) REFERENCES document_snapshots(project_id, document_snapshot_id),
    CONSTRAINT snapshot_memberships_document_version_fk FOREIGN KEY (
        project_id,
        document_version_id
    ) REFERENCES document_versions(project_id, document_version_id),
    CONSTRAINT snapshot_memberships_identity PRIMARY KEY (
        document_snapshot_id,
        document_version_id
    ),
    CONSTRAINT snapshot_memberships_project_identity_unique UNIQUE (
        project_id,
        document_snapshot_id,
        document_version_id
    )
);

CREATE TABLE document_facts (
    document_fact_id text PRIMARY KEY,
    project_id text NOT NULL,
    document_snapshot_id text NOT NULL,
    document_version_id text NOT NULL,
    stable_key text NOT NULL,
    fact_type text NOT NULL,
    values_json jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    field_evidence jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_facts_identity_not_blank CHECK (
        btrim(stable_key) <> '' AND btrim(fact_type) <> ''
    ),
    CONSTRAINT document_facts_stable_key_type_prefix CHECK (
        left(stable_key, length(fact_type) + 1) = fact_type || ':'
    ),
    CONSTRAINT document_facts_values_object CHECK (
        jsonb_typeof(values_json) = 'object' AND values_json <> '{}'::jsonb
    ),
    CONSTRAINT document_facts_source_refs_array CHECK (
        jsonb_typeof(source_refs) = 'array' AND jsonb_array_length(source_refs) > 0
    ),
    CONSTRAINT document_facts_field_evidence_array CHECK (
        jsonb_typeof(field_evidence) = 'array'
    ),
    CONSTRAINT document_facts_membership_fk FOREIGN KEY (
        project_id,
        document_snapshot_id,
        document_version_id
    ) REFERENCES snapshot_memberships(
        project_id,
        document_snapshot_id,
        document_version_id
    ),
    CONSTRAINT document_facts_snapshot_stable_key_unique UNIQUE (
        document_snapshot_id,
        stable_key
    ),
    CONSTRAINT document_facts_change_reference_unique UNIQUE (
        project_id,
        document_snapshot_id,
        document_fact_id,
        stable_key,
        fact_type
    )
);

CREATE INDEX document_facts_snapshot_type_idx
    ON document_facts (document_snapshot_id, fact_type, stable_key);

CREATE TABLE structured_changes (
    structured_change_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    source_snapshot_id text NOT NULL,
    target_snapshot_id text NOT NULL,
    stable_key text NOT NULL,
    fact_type text NOT NULL,
    domain text NOT NULL,
    change_type text NOT NULL,
    before_fact_id text,
    after_fact_id text,
    summary text NOT NULL,
    source_refs jsonb NOT NULL,
    confidence text NOT NULL,
    review_status text NOT NULL,
    unknowns jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT structured_changes_snapshot_pair_valid CHECK (
        source_snapshot_id <> target_snapshot_id
    ),
    CONSTRAINT structured_changes_identity_not_blank CHECK (
        btrim(stable_key) <> ''
        AND btrim(fact_type) <> ''
        AND btrim(domain) <> ''
        AND btrim(summary) <> ''
    ),
    CONSTRAINT structured_changes_type_valid CHECK (
        change_type IN ('added', 'modified', 'deleted')
    ),
    CONSTRAINT structured_changes_state_valid CHECK (
        (change_type = 'added' AND before_fact_id IS NULL AND after_fact_id IS NOT NULL)
        OR (change_type = 'modified' AND before_fact_id IS NOT NULL AND after_fact_id IS NOT NULL)
        OR (change_type = 'deleted' AND before_fact_id IS NOT NULL AND after_fact_id IS NULL)
    ),
    CONSTRAINT structured_changes_confidence_valid CHECK (
        confidence IN ('high', 'medium', 'low')
    ),
    CONSTRAINT structured_changes_review_status_valid CHECK (
        review_status IN ('accepted', 'needs_review', 'rejected')
    ),
    CONSTRAINT structured_changes_source_refs_array CHECK (
        jsonb_typeof(source_refs) = 'array' AND jsonb_array_length(source_refs) > 0
    ),
    CONSTRAINT structured_changes_unknowns_array CHECK (jsonb_typeof(unknowns) = 'array'),
    CONSTRAINT structured_changes_source_snapshot_fk FOREIGN KEY (
        project_id,
        source_snapshot_id
    ) REFERENCES document_snapshots(project_id, document_snapshot_id),
    CONSTRAINT structured_changes_target_snapshot_fk FOREIGN KEY (
        project_id,
        target_snapshot_id
    ) REFERENCES document_snapshots(project_id, document_snapshot_id),
    CONSTRAINT structured_changes_before_fact_fk FOREIGN KEY (
        project_id,
        source_snapshot_id,
        before_fact_id,
        stable_key,
        fact_type
    ) REFERENCES document_facts(
        project_id,
        document_snapshot_id,
        document_fact_id,
        stable_key,
        fact_type
    ),
    CONSTRAINT structured_changes_after_fact_fk FOREIGN KEY (
        project_id,
        target_snapshot_id,
        after_fact_id,
        stable_key,
        fact_type
    ) REFERENCES document_facts(
        project_id,
        document_snapshot_id,
        document_fact_id,
        stable_key,
        fact_type
    ),
    CONSTRAINT structured_changes_pair_stable_key_unique UNIQUE (
        project_id,
        source_snapshot_id,
        target_snapshot_id,
        stable_key
    )
);

CREATE INDEX structured_changes_project_pair_idx
    ON structured_changes (project_id, source_snapshot_id, target_snapshot_id, change_type);
