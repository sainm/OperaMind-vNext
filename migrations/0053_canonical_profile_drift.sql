ALTER TABLE profile_versions
    DROP CONSTRAINT profile_versions_type_valid;

ALTER TABLE profile_versions
    ADD CONSTRAINT profile_versions_type_valid CHECK (
        profile_type IN (
            'EmbeddingProfile',
            'DocumentConventionProfile',
            'DocumentRelationProfile',
            'CodeFrameworkProfile',
            'CommandExecutionProfile',
            'UiLocatorProfile'
        )
    );

CREATE TABLE profile_drift_events (
    profile_drift_event_id text PRIMARY KEY,
    activation_event_id text NOT NULL UNIQUE
        REFERENCES profile_activation_events(activation_event_id),
    project_id text NOT NULL REFERENCES projects(project_id),
    binding_key text NOT NULL,
    previous_profile_version_id text NOT NULL REFERENCES profile_versions(profile_version_id),
    activated_profile_version_id text NOT NULL REFERENCES profile_versions(profile_version_id),
    status text NOT NULL DEFAULT 'open',
    detected_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    CONSTRAINT profile_drift_events_fields_not_blank CHECK (
        btrim(profile_drift_event_id) <> '' AND btrim(binding_key) <> ''
    ),
    CONSTRAINT profile_drift_events_version_changed CHECK (
        previous_profile_version_id <> activated_profile_version_id
    ),
    CONSTRAINT profile_drift_events_status_valid CHECK (status IN ('open', 'resolved')),
    CONSTRAINT profile_drift_events_resolution_consistent CHECK (
        (status = 'open' AND resolved_at IS NULL)
        OR (status = 'resolved' AND resolved_at IS NOT NULL)
    )
);

CREATE TABLE profile_drift_impacts (
    profile_drift_event_id text NOT NULL
        REFERENCES profile_drift_events(profile_drift_event_id),
    project_id text NOT NULL REFERENCES projects(project_id),
    affected_layer text NOT NULL,
    artifact_type text NOT NULL,
    artifact_id text NOT NULL,
    effective_status text NOT NULL,
    reason text NOT NULL,
    rebuild_action text NOT NULL,
    resolved_at timestamptz,
    CONSTRAINT profile_drift_impacts_fields_not_blank CHECK (
        btrim(artifact_type) <> ''
        AND btrim(artifact_id) <> ''
        AND btrim(reason) <> ''
        AND btrim(rebuild_action) <> ''
    ),
    CONSTRAINT profile_drift_impacts_layer_valid CHECK (
        affected_layer IN ('snapshot', 'impact', 'test_plan', 'evidence', 'closure')
    ),
    CONSTRAINT profile_drift_impacts_status_valid CHECK (
        effective_status IN ('stale', 'blocked')
    ),
    CONSTRAINT profile_drift_impacts_identity PRIMARY KEY (
        profile_drift_event_id, artifact_type, artifact_id
    ),
    CONSTRAINT profile_drift_impacts_project_identity_unique UNIQUE (
        profile_drift_event_id, project_id, artifact_type, artifact_id
    )
);

CREATE INDEX profile_drift_impacts_project_open_idx
    ON profile_drift_impacts (project_id, affected_layer, artifact_type, artifact_id)
    WHERE resolved_at IS NULL;

CREATE TABLE profile_rebuild_requests (
    profile_rebuild_request_id text PRIMARY KEY,
    profile_drift_event_id text NOT NULL,
    project_id text NOT NULL,
    artifact_type text NOT NULL,
    artifact_id text NOT NULL,
    rebuild_action text NOT NULL,
    status text NOT NULL DEFAULT 'requested',
    requested_by text NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CONSTRAINT profile_rebuild_requests_fields_not_blank CHECK (
        btrim(profile_rebuild_request_id) <> ''
        AND btrim(artifact_type) <> ''
        AND btrim(artifact_id) <> ''
        AND btrim(rebuild_action) <> ''
        AND btrim(requested_by) <> ''
    ),
    CONSTRAINT profile_rebuild_requests_status_valid CHECK (
        status IN ('requested', 'in_progress', 'completed', 'failed')
    ),
    CONSTRAINT profile_rebuild_requests_completion_consistent CHECK (
        (status IN ('requested', 'in_progress') AND completed_at IS NULL)
        OR (status IN ('completed', 'failed') AND completed_at IS NOT NULL)
    ),
    CONSTRAINT profile_rebuild_requests_impact_fk FOREIGN KEY (
        profile_drift_event_id, project_id, artifact_type, artifact_id
    ) REFERENCES profile_drift_impacts (
        profile_drift_event_id, project_id, artifact_type, artifact_id
    )
);

CREATE UNIQUE INDEX profile_rebuild_requests_open_unique
    ON profile_rebuild_requests (
        profile_drift_event_id, artifact_type, artifact_id
    ) WHERE status IN ('requested', 'in_progress');

CREATE INDEX profile_rebuild_requests_project_idx
    ON profile_rebuild_requests (project_id, requested_at DESC);
