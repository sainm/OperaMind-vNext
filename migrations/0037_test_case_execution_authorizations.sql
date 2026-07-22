CREATE TABLE test_case_execution_authorizations (
    authorization_id text PRIMARY KEY,
    revision_id text NOT NULL,
    target_orchestration_id text NOT NULL,
    approval_grant_id text NOT NULL,
    project_id text NOT NULL,
    decision text NOT NULL,
    source_scope_digest text NOT NULL,
    target_scope_digest text NOT NULL,
    changed_dimensions jsonb NOT NULL,
    confirmed_by text NOT NULL,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT test_case_execution_authorizations_fields_not_blank CHECK (
        btrim(authorization_id) <> ''
        AND btrim(confirmed_by) <> ''
    ),
    CONSTRAINT test_case_execution_authorizations_decision_valid CHECK (
        decision IN ('reused', 'reconfirmed')
    ),
    CONSTRAINT test_case_execution_authorizations_digests_valid CHECK (
        source_scope_digest ~ '^[0-9a-f]{64}$'
        AND target_scope_digest ~ '^[0-9a-f]{64}$'
        AND payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT test_case_execution_authorizations_dimensions_valid CHECK (
        jsonb_typeof(changed_dimensions) = 'array'
        AND (
            (decision = 'reused' AND jsonb_array_length(changed_dimensions) = 0)
            OR
            (decision = 'reconfirmed' AND jsonb_array_length(changed_dimensions) > 0)
        )
    ),
    CONSTRAINT test_case_execution_authorizations_revision_fk FOREIGN KEY (
        revision_id, project_id
    ) REFERENCES test_case_revisions(revision_id, project_id),
    CONSTRAINT test_case_execution_authorizations_orchestration_fk FOREIGN KEY (
        target_orchestration_id, project_id
    ) REFERENCES change_orchestrations(orchestration_id, project_id),
    CONSTRAINT test_case_execution_authorizations_grant_fk FOREIGN KEY (
        approval_grant_id, project_id
    ) REFERENCES approval_grants(approval_grant_id, project_id),
    CONSTRAINT test_case_execution_authorizations_identity_unique UNIQUE (
        revision_id, approval_grant_id, target_scope_digest, decision
    ),
    CONSTRAINT test_case_execution_authorizations_scope_unique UNIQUE (
        authorization_id, project_id
    )
);

CREATE INDEX test_case_execution_authorizations_target_idx
    ON test_case_execution_authorizations (
        project_id, target_orchestration_id, created_at DESC, authorization_id DESC
    );
