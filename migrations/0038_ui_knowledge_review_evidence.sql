ALTER TABLE ui_locator_observations
    ADD CONSTRAINT ui_locator_observations_evidence_scope_unique UNIQUE (
        ui_locator_observation_id,
        ui_locator_observation_run_id,
        project_id,
        source_ui_knowledge_snapshot_id,
        target_ref
    );

CREATE TABLE ui_locator_observation_evidence (
    evidence_id text PRIMARY KEY,
    ui_locator_observation_run_id text NOT NULL,
    ui_locator_observation_id text NOT NULL,
    project_id text NOT NULL,
    source_ui_knowledge_snapshot_id text NOT NULL,
    target_ref text NOT NULL,
    evidence_ref text NOT NULL,
    content_digest text NOT NULL,
    sanitized boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_locator_observation_evidence_fields_not_blank CHECK (
        btrim(evidence_id) <> ''
        AND btrim(evidence_ref) <> ''
    ),
    CONSTRAINT ui_locator_observation_evidence_digest_valid CHECK (
        content_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ui_locator_observation_evidence_run_fk FOREIGN KEY (
        ui_locator_observation_run_id, project_id
    ) REFERENCES ui_locator_observation_runs(
        ui_locator_observation_run_id, project_id
    ),
    CONSTRAINT ui_locator_observation_evidence_observation_fk FOREIGN KEY (
        ui_locator_observation_id,
        ui_locator_observation_run_id,
        project_id,
        source_ui_knowledge_snapshot_id,
        target_ref
    ) REFERENCES ui_locator_observations(
        ui_locator_observation_id,
        ui_locator_observation_run_id,
        project_id,
        source_ui_knowledge_snapshot_id,
        target_ref
    ),
    CONSTRAINT ui_locator_observation_evidence_target_fk FOREIGN KEY (
        source_ui_knowledge_snapshot_id, project_id, target_ref
    ) REFERENCES ui_knowledge_targets(
        ui_knowledge_snapshot_id, project_id, target_ref
    ),
    CONSTRAINT ui_locator_observation_evidence_target_unique UNIQUE (
        ui_locator_observation_run_id, target_ref
    )
);

CREATE INDEX ui_locator_observation_evidence_review_idx
    ON ui_locator_observation_evidence (
        project_id,
        source_ui_knowledge_snapshot_id,
        target_ref,
        created_at DESC
    );
