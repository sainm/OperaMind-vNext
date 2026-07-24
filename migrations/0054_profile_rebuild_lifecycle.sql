CREATE TABLE profile_rebuild_batches (
    profile_rebuild_batch_id text PRIMARY KEY,
    profile_drift_event_id text NOT NULL
        REFERENCES profile_drift_events(profile_drift_event_id),
    project_id text NOT NULL REFERENCES projects(project_id),
    status text NOT NULL DEFAULT 'requested',
    requested_by text NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CONSTRAINT profile_rebuild_batches_fields_not_blank CHECK (
        btrim(profile_rebuild_batch_id) <> '' AND btrim(requested_by) <> ''
    ),
    CONSTRAINT profile_rebuild_batches_status_valid CHECK (
        status IN ('requested', 'in_progress', 'completed', 'failed', 'blocked')
    ),
    CONSTRAINT profile_rebuild_batches_completion_consistent CHECK (
        (status IN ('requested', 'in_progress') AND completed_at IS NULL)
        OR (status IN ('completed', 'failed', 'blocked') AND completed_at IS NOT NULL)
    ),
    CONSTRAINT profile_rebuild_batches_scope_unique UNIQUE (
        profile_rebuild_batch_id, project_id
    )
);

INSERT INTO profile_rebuild_batches (
    profile_rebuild_batch_id, profile_drift_event_id, project_id,
    status, requested_by, requested_at, completed_at
)
SELECT min(profile_rebuild_request_id),
       profile_drift_event_id,
       project_id,
       CASE
           WHEN bool_or(status IN ('requested', 'in_progress')) THEN 'requested'
           WHEN bool_or(status = 'failed') THEN 'failed'
           ELSE 'blocked'
       END,
       (array_agg(requested_by ORDER BY requested_at, profile_rebuild_request_id))[1],
       min(requested_at),
       CASE
           WHEN bool_or(status IN ('requested', 'in_progress')) THEN NULL
           ELSE max(completed_at)
       END
FROM profile_rebuild_requests
GROUP BY profile_drift_event_id, project_id;

ALTER TABLE profile_rebuild_requests
    ADD COLUMN profile_rebuild_batch_id text,
    ADD COLUMN phase_order integer,
    ADD COLUMN attempt_count integer NOT NULL DEFAULT 0,
    ADD COLUMN max_attempts integer NOT NULL DEFAULT 3,
    ADD COLUMN lease_seconds integer NOT NULL DEFAULT 300,
    ADD COLUMN last_error text,
    ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

UPDATE profile_rebuild_requests AS request
SET profile_rebuild_batch_id = batch.profile_rebuild_batch_id,
    phase_order = CASE impact.affected_layer
        WHEN 'snapshot' THEN 10
        WHEN 'impact' THEN 20
        WHEN 'test_plan' THEN 30
        WHEN 'evidence' THEN 40
        WHEN 'closure' THEN 50
    END
FROM profile_drift_impacts AS impact
JOIN profile_rebuild_batches AS batch
  ON batch.profile_drift_event_id = impact.profile_drift_event_id
 AND batch.project_id = impact.project_id
WHERE impact.profile_drift_event_id = request.profile_drift_event_id
  AND impact.project_id = request.project_id
  AND impact.artifact_type = request.artifact_type
  AND impact.artifact_id = request.artifact_id;

ALTER TABLE profile_rebuild_requests
    DROP CONSTRAINT profile_rebuild_requests_status_valid,
    DROP CONSTRAINT profile_rebuild_requests_completion_consistent;

UPDATE profile_rebuild_requests
SET status = 'requested', completed_at = NULL, updated_at = now()
WHERE status = 'in_progress';

UPDATE profile_rebuild_batches
SET status = 'requested', completed_at = NULL
WHERE status = 'in_progress';

UPDATE profile_rebuild_requests
SET status = 'blocked',
    last_error = 'Legacy completion requires Canonical replacement validation',
    updated_at = now()
WHERE status = 'completed';

UPDATE profile_rebuild_batches
SET status = 'blocked', completed_at = COALESCE(completed_at, now())
WHERE status = 'completed';

UPDATE profile_rebuild_requests
SET last_error = 'Legacy rebuild failure imported during lifecycle migration',
    updated_at = now()
WHERE status = 'failed';

ALTER TABLE profile_rebuild_requests
    ALTER COLUMN profile_rebuild_batch_id SET NOT NULL,
    ALTER COLUMN phase_order SET NOT NULL,
    ADD CONSTRAINT profile_rebuild_requests_batch_fk FOREIGN KEY (
        profile_rebuild_batch_id, project_id
    ) REFERENCES profile_rebuild_batches(profile_rebuild_batch_id, project_id),
    ADD CONSTRAINT profile_rebuild_requests_phase_valid CHECK (
        phase_order IN (10, 20, 30, 40, 50)
    ),
    ADD CONSTRAINT profile_rebuild_requests_attempts_valid CHECK (
        max_attempts BETWEEN 1 AND 100
        AND attempt_count BETWEEN 0 AND max_attempts
        AND lease_seconds BETWEEN 30 AND 86400
    ),
    ADD CONSTRAINT profile_rebuild_requests_status_valid CHECK (
        status IN ('requested', 'in_progress', 'completed', 'failed', 'blocked')
    ),
    ADD CONSTRAINT profile_rebuild_requests_completion_consistent CHECK (
        (status IN ('requested', 'in_progress') AND completed_at IS NULL)
        OR (status IN ('completed', 'failed', 'blocked') AND completed_at IS NOT NULL)
    ),
    ADD CONSTRAINT profile_rebuild_requests_error_consistent CHECK (
        (status IN ('failed', 'blocked') AND last_error IS NOT NULL AND btrim(last_error) <> '')
        OR (status NOT IN ('failed', 'blocked') AND last_error IS NULL)
    ),
    ADD CONSTRAINT profile_rebuild_requests_scope_unique UNIQUE (
        profile_rebuild_request_id, project_id
    );

CREATE TABLE profile_rebuild_request_dependencies (
    profile_rebuild_request_id text NOT NULL,
    depends_on_request_id text NOT NULL,
    project_id text NOT NULL REFERENCES projects(project_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_rebuild_request_id, depends_on_request_id),
    CONSTRAINT profile_rebuild_request_dependencies_not_self CHECK (
        profile_rebuild_request_id <> depends_on_request_id
    ),
    CONSTRAINT profile_rebuild_request_dependencies_request_fk FOREIGN KEY (
        profile_rebuild_request_id, project_id
    ) REFERENCES profile_rebuild_requests(profile_rebuild_request_id, project_id),
    CONSTRAINT profile_rebuild_request_dependencies_parent_fk FOREIGN KEY (
        depends_on_request_id, project_id
    ) REFERENCES profile_rebuild_requests(profile_rebuild_request_id, project_id)
);

CREATE TABLE profile_rebuild_claims (
    profile_rebuild_claim_id text PRIMARY KEY,
    profile_rebuild_request_id text NOT NULL,
    project_id text NOT NULL REFERENCES projects(project_id),
    executor_kind text NOT NULL,
    executor_id text NOT NULL,
    lease_token_digest text NOT NULL,
    status text NOT NULL,
    claimed_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz NOT NULL,
    released_at timestamptz,
    release_reason text,
    CONSTRAINT profile_rebuild_claims_fields_not_blank CHECK (
        btrim(profile_rebuild_claim_id) <> ''
        AND btrim(executor_id) <> ''
        AND (release_reason IS NULL OR btrim(release_reason) <> '')
    ),
    CONSTRAINT profile_rebuild_claims_executor_valid CHECK (
        executor_kind IN ('agent', 'subagent')
    ),
    CONSTRAINT profile_rebuild_claims_status_valid CHECK (
        status IN ('active', 'completed', 'released', 'expired')
    ),
    CONSTRAINT profile_rebuild_claims_digest_valid CHECK (
        lease_token_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT profile_rebuild_claims_lease_valid CHECK (
        lease_expires_at > claimed_at
        AND (
            (status = 'active' AND released_at IS NULL AND release_reason IS NULL)
            OR (status <> 'active' AND released_at IS NOT NULL)
        )
    ),
    CONSTRAINT profile_rebuild_claims_request_fk FOREIGN KEY (
        profile_rebuild_request_id, project_id
    ) REFERENCES profile_rebuild_requests(profile_rebuild_request_id, project_id)
);

CREATE UNIQUE INDEX profile_rebuild_claims_one_active_idx
    ON profile_rebuild_claims(profile_rebuild_request_id)
    WHERE status = 'active';

CREATE TABLE profile_artifact_replacements (
    profile_rebuild_request_id text PRIMARY KEY,
    profile_drift_event_id text NOT NULL,
    project_id text NOT NULL,
    replaced_artifact_type text NOT NULL,
    replaced_artifact_id text NOT NULL,
    replacement_artifact_type text NOT NULL,
    replacement_artifact_id text NOT NULL,
    validation_evidence jsonb NOT NULL,
    validation_digest text NOT NULL,
    validated_by text NOT NULL,
    validated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT profile_artifact_replacements_fields_not_blank CHECK (
        btrim(replaced_artifact_type) <> ''
        AND btrim(replaced_artifact_id) <> ''
        AND btrim(replacement_artifact_type) <> ''
        AND btrim(replacement_artifact_id) <> ''
        AND btrim(validated_by) <> ''
    ),
    CONSTRAINT profile_artifact_replacements_not_same CHECK (
        replaced_artifact_type <> replacement_artifact_type
        OR replaced_artifact_id <> replacement_artifact_id
    ),
    CONSTRAINT profile_artifact_replacements_evidence_valid CHECK (
        jsonb_typeof(validation_evidence) = 'object'
        AND validation_evidence <> '{}'::jsonb
        AND validation_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT profile_artifact_replacements_impact_fk FOREIGN KEY (
        profile_drift_event_id, project_id,
        replaced_artifact_type, replaced_artifact_id
    ) REFERENCES profile_drift_impacts (
        profile_drift_event_id, project_id, artifact_type, artifact_id
    ),
    CONSTRAINT profile_artifact_replacements_scope_unique UNIQUE (
        profile_drift_event_id, project_id,
        replacement_artifact_type, replacement_artifact_id
    ),
    CONSTRAINT profile_artifact_replacements_request_fk FOREIGN KEY (
        profile_rebuild_request_id, project_id
    ) REFERENCES profile_rebuild_requests(profile_rebuild_request_id, project_id)
);

CREATE TABLE profile_rebuild_events (
    profile_rebuild_event_id text PRIMARY KEY,
    profile_rebuild_request_id text NOT NULL,
    project_id text NOT NULL REFERENCES projects(project_id),
    sequence integer NOT NULL,
    event_type text NOT NULL,
    actor text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT profile_rebuild_events_fields_not_blank CHECK (
        btrim(profile_rebuild_event_id) <> ''
        AND sequence >= 1
        AND btrim(actor) <> ''
    ),
    CONSTRAINT profile_rebuild_events_type_valid CHECK (
        event_type IN (
            'scheduled', 'claimed', 'heartbeat', 'released', 'lease_expired',
            'completed', 'failed', 'blocked', 'requeued'
        )
    ),
    CONSTRAINT profile_rebuild_events_payload_valid CHECK (
        jsonb_typeof(payload) = 'object'
        AND payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT profile_rebuild_events_sequence_unique UNIQUE (
        profile_rebuild_request_id, sequence
    ),
    CONSTRAINT profile_rebuild_events_request_fk FOREIGN KEY (
        profile_rebuild_request_id, project_id
    ) REFERENCES profile_rebuild_requests(profile_rebuild_request_id, project_id)
);

CREATE UNIQUE INDEX profile_rebuild_batches_one_active_event_idx
    ON profile_rebuild_batches(profile_drift_event_id)
    WHERE status IN ('requested', 'in_progress');

CREATE INDEX profile_rebuild_requests_ready_idx
    ON profile_rebuild_requests(project_id, status, phase_order, requested_at)
    WHERE status IN ('requested', 'in_progress');

CREATE INDEX profile_rebuild_dependencies_request_idx
    ON profile_rebuild_request_dependencies(profile_rebuild_request_id);

CREATE INDEX profile_rebuild_events_request_idx
    ON profile_rebuild_events(profile_rebuild_request_id, sequence);
