CREATE TABLE ui_knowledge_snapshots (
    ui_knowledge_snapshot_id text PRIMARY KEY,
    project_id text NOT NULL,
    environment_id text NOT NULL,
    deployment_revision text NOT NULL,
    snapshot_version text NOT NULL,
    review_status text NOT NULL,
    reviewed_by text,
    payload_digest text NOT NULL,
    is_active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_knowledge_snapshots_fields_not_blank CHECK (
        btrim(ui_knowledge_snapshot_id) <> ''
        AND btrim(snapshot_version) <> ''
    ),
    CONSTRAINT ui_knowledge_snapshots_review_valid CHECK (
        review_status IN ('draft', 'approved', 'rejected')
        AND (
            (review_status = 'draft' AND reviewed_by IS NULL)
            OR (review_status <> 'draft' AND reviewed_by IS NOT NULL AND btrim(reviewed_by) <> '')
        )
        AND (NOT is_active OR review_status = 'approved')
    ),
    CONSTRAINT ui_knowledge_snapshots_digest_sha256 CHECK (
        payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ui_knowledge_snapshots_deployment_fk FOREIGN KEY (
        deployment_revision, environment_id, project_id
    ) REFERENCES ui_deployments(deployment_revision, environment_id, project_id),
    CONSTRAINT ui_knowledge_snapshots_scope_identity_unique UNIQUE (
        ui_knowledge_snapshot_id, project_id
    ),
    CONSTRAINT ui_knowledge_snapshots_version_unique UNIQUE (
        project_id, environment_id, deployment_revision, snapshot_version
    )
);

CREATE UNIQUE INDEX ui_knowledge_snapshots_active_deployment_unique
    ON ui_knowledge_snapshots (project_id, environment_id, deployment_revision)
    WHERE is_active;

CREATE TABLE ui_knowledge_targets (
    ui_knowledge_snapshot_id text NOT NULL,
    project_id text NOT NULL,
    target_ref text NOT NULL,
    business_name text NOT NULL,
    screen_name text NOT NULL,
    trigger_path text,
    source_fact_refs jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_knowledge_targets_fields_not_blank CHECK (
        btrim(target_ref) <> ''
        AND btrim(business_name) <> ''
        AND btrim(screen_name) <> ''
        AND (trigger_path IS NULL OR btrim(trigger_path) <> '')
    ),
    CONSTRAINT ui_knowledge_targets_sources_valid CHECK (
        jsonb_typeof(source_fact_refs) = 'array'
        AND jsonb_array_length(source_fact_refs) > 0
    ),
    CONSTRAINT ui_knowledge_targets_snapshot_fk FOREIGN KEY (
        ui_knowledge_snapshot_id, project_id
    ) REFERENCES ui_knowledge_snapshots(ui_knowledge_snapshot_id, project_id),
    CONSTRAINT ui_knowledge_targets_identity PRIMARY KEY (
        ui_knowledge_snapshot_id, target_ref
    ),
    CONSTRAINT ui_knowledge_targets_scope_identity_unique UNIQUE (
        ui_knowledge_snapshot_id, project_id, target_ref
    )
);

CREATE TABLE ui_locator_candidates (
    locator_candidate_id text PRIMARY KEY,
    ui_knowledge_snapshot_id text NOT NULL,
    project_id text NOT NULL,
    target_ref text NOT NULL,
    strategy text NOT NULL,
    locator_value text NOT NULL,
    accessible_name text,
    exact_match boolean NOT NULL,
    priority integer NOT NULL,
    reliability_score numeric(4, 3) NOT NULL,
    source text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_locator_candidates_fields_not_blank CHECK (
        btrim(locator_candidate_id) <> ''
        AND btrim(locator_value) <> ''
        AND btrim(source) <> ''
        AND (accessible_name IS NULL OR btrim(accessible_name) <> '')
    ),
    CONSTRAINT ui_locator_candidates_strategy_valid CHECK (
        strategy IN ('role', 'label', 'text', 'test_id', 'placeholder', 'css')
    ),
    CONSTRAINT ui_locator_candidates_role_name_valid CHECK (
        (strategy = 'role' AND accessible_name IS NOT NULL)
        OR (strategy <> 'role' AND accessible_name IS NULL)
    ),
    CONSTRAINT ui_locator_candidates_rank_valid CHECK (
        priority >= 1 AND reliability_score BETWEEN 0 AND 1
    ),
    CONSTRAINT ui_locator_candidates_target_fk FOREIGN KEY (
        ui_knowledge_snapshot_id, project_id, target_ref
    ) REFERENCES ui_knowledge_targets(ui_knowledge_snapshot_id, project_id, target_ref),
    CONSTRAINT ui_locator_candidates_priority_unique UNIQUE (
        ui_knowledge_snapshot_id, target_ref, priority
    )
);

ALTER TABLE ui_browser_manifests
    ADD COLUMN ui_knowledge_snapshot_id text,
    ADD CONSTRAINT ui_browser_manifests_knowledge_fk FOREIGN KEY (
        ui_knowledge_snapshot_id, project_id
    ) REFERENCES ui_knowledge_snapshots(ui_knowledge_snapshot_id, project_id);

ALTER TABLE ui_browser_scenario_specs
    ADD COLUMN preflight_assertions jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD CONSTRAINT ui_browser_scenario_specs_preflight_assertions_valid CHECK (
        jsonb_typeof(preflight_assertions) = 'array'
    );

CREATE INDEX ui_knowledge_targets_business_name_idx
    ON ui_knowledge_targets (project_id, business_name);

CREATE INDEX ui_locator_candidates_target_idx
    ON ui_locator_candidates (
        project_id, ui_knowledge_snapshot_id, target_ref, reliability_score DESC, priority
    );
