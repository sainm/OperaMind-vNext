CREATE TABLE ui_knowledge_review_events (
    ui_knowledge_review_event_id text PRIMARY KEY,
    project_id text NOT NULL,
    source_ui_knowledge_snapshot_id text NOT NULL,
    result_ui_knowledge_snapshot_id text NOT NULL,
    decision text NOT NULL,
    reviewed_by text NOT NULL,
    reason text,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_knowledge_review_events_fields_not_blank CHECK (
        btrim(ui_knowledge_review_event_id) <> ''
        AND btrim(reviewed_by) <> ''
        AND (reason IS NULL OR btrim(reason) <> '')
        AND source_ui_knowledge_snapshot_id <> result_ui_knowledge_snapshot_id
    ),
    CONSTRAINT ui_knowledge_review_events_decision_valid CHECK (
        decision IN ('approved', 'rejected')
    ),
    CONSTRAINT ui_knowledge_review_events_digest_sha256 CHECK (
        payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ui_knowledge_review_events_source_fk FOREIGN KEY (
        source_ui_knowledge_snapshot_id, project_id
    ) REFERENCES ui_knowledge_snapshots(ui_knowledge_snapshot_id, project_id),
    CONSTRAINT ui_knowledge_review_events_result_fk FOREIGN KEY (
        result_ui_knowledge_snapshot_id, project_id
    ) REFERENCES ui_knowledge_snapshots(ui_knowledge_snapshot_id, project_id),
    CONSTRAINT ui_knowledge_review_events_scope_identity_unique UNIQUE (
        ui_knowledge_review_event_id, project_id
    ),
    CONSTRAINT ui_knowledge_review_events_source_result_unique UNIQUE (
        source_ui_knowledge_snapshot_id, result_ui_knowledge_snapshot_id
    )
);

CREATE INDEX ui_knowledge_review_events_source_idx
    ON ui_knowledge_review_events (
        project_id, source_ui_knowledge_snapshot_id, created_at DESC
    );
