CREATE TABLE readiness_observations (
    observation_id text PRIMARY KEY,
    gate_id text NOT NULL,
    evidence_type text NOT NULL,
    project_id text REFERENCES projects(project_id),
    analysis_case_id text,
    observed_at timestamptz NOT NULL,
    review_status text NOT NULL,
    reviewed_by jsonb NOT NULL,
    subject jsonb NOT NULL,
    subject_digest text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT readiness_observations_fields_not_blank CHECK (
        btrim(observation_id) <> ''
    ),
    CONSTRAINT readiness_observations_gate_valid CHECK (
        gate_id IN (
            'embedding_provider_live',
            'github_copilot_live',
            'full_local_regression'
        )
    ),
    CONSTRAINT readiness_observations_type_valid CHECK (
        (gate_id = 'embedding_provider_live' AND evidence_type = 'provider_probe')
        OR (gate_id = 'github_copilot_live' AND evidence_type = 'copilot_session')
        OR (gate_id = 'full_local_regression' AND evidence_type = 'test_report')
    ),
    CONSTRAINT readiness_observations_scope_valid CHECK (
        (gate_id = 'full_local_regression' AND project_id IS NULL AND analysis_case_id IS NULL)
        OR (
            gate_id = 'embedding_provider_live'
            AND project_id IS NOT NULL
            AND analysis_case_id IS NULL
        )
        OR (
            gate_id = 'github_copilot_live'
            AND project_id IS NOT NULL
            AND analysis_case_id IS NOT NULL
        )
    ),
    CONSTRAINT readiness_observations_review_valid CHECK (
        review_status IN ('reviewed', 'verified')
        AND jsonb_typeof(reviewed_by) = 'array'
        AND jsonb_array_length(reviewed_by) > 0
    ),
    CONSTRAINT readiness_observations_subject_object CHECK (
        jsonb_typeof(subject) = 'object'
    ),
    CONSTRAINT readiness_observations_digest_sha256 CHECK (
        subject_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT readiness_observations_case_fk FOREIGN KEY (
        analysis_case_id, project_id
    ) REFERENCES analysis_cases(analysis_case_id, project_id)
);

CREATE UNIQUE INDEX readiness_observations_replay_unique
    ON readiness_observations (
        gate_id,
        COALESCE(project_id, ''),
        COALESCE(analysis_case_id, ''),
        subject_digest
    );

CREATE INDEX readiness_observations_latest_idx
    ON readiness_observations (
        gate_id,
        project_id,
        analysis_case_id,
        observed_at DESC,
        observation_id DESC
    );
