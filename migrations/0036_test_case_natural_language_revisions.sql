ALTER TABLE change_orchestrations
    DROP CONSTRAINT change_orchestrations_status_valid,
    DROP CONSTRAINT change_orchestrations_basis_unique;

-- A natural-language Test Case revision keeps the confirmed design and impact
-- basis while creating a new immutable planning bundle.  The old basis-level
-- uniqueness from 0031 would therefore reject every legitimate second version.
-- Current-version uniqueness is represented explicitly by the ready/blocked vs.
-- superseded state and the self-referencing supersession edge below.

ALTER TABLE change_orchestrations
    ADD COLUMN superseded_by_orchestration_id text,
    ADD COLUMN superseded_at timestamptz,
    ADD CONSTRAINT change_orchestrations_status_valid CHECK (
        status IN ('ready', 'blocked', 'superseded')
    ),
    ADD CONSTRAINT change_orchestrations_supersession_consistent CHECK (
        (
            status = 'superseded'
            AND superseded_by_orchestration_id IS NOT NULL
            AND superseded_at IS NOT NULL
        )
        OR (
            status <> 'superseded'
            AND superseded_by_orchestration_id IS NULL
            AND superseded_at IS NULL
        )
    ),
    ADD CONSTRAINT change_orchestrations_superseded_by_fk FOREIGN KEY (
        superseded_by_orchestration_id, project_id
    ) REFERENCES change_orchestrations(orchestration_id, project_id);

CREATE TABLE test_case_change_proposals (
    proposal_id text PRIMARY KEY,
    change_request_id text NOT NULL,
    project_id text NOT NULL,
    source_orchestration_id text NOT NULL,
    source_test_plan_id text NOT NULL,
    analysis_status text NOT NULL,
    instruction_digest text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT test_case_change_proposals_fields_not_blank CHECK (
        btrim(proposal_id) <> ''
        AND btrim(source_test_plan_id) <> ''
        AND btrim(created_by) <> ''
    ),
    CONSTRAINT test_case_change_proposals_status_valid CHECK (
        analysis_status IN ('deterministic', 'needs_confirmation', 'blocked')
    ),
    CONSTRAINT test_case_change_proposals_digest_sha256 CHECK (
        instruction_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT test_case_change_proposals_artifact_fk FOREIGN KEY (proposal_id)
        REFERENCES artifact_records(artifact_id),
    CONSTRAINT test_case_change_proposals_request_fk FOREIGN KEY (
        change_request_id, project_id
    ) REFERENCES change_requests(change_request_id, project_id),
    CONSTRAINT test_case_change_proposals_orchestration_fk FOREIGN KEY (
        source_orchestration_id, project_id
    ) REFERENCES change_orchestrations(orchestration_id, project_id),
    CONSTRAINT test_case_change_proposals_scope_unique UNIQUE (
        proposal_id, project_id
    )
);

CREATE TABLE test_case_revisions (
    revision_id text PRIMARY KEY,
    proposal_id text NOT NULL UNIQUE,
    change_request_id text NOT NULL,
    project_id text NOT NULL,
    source_orchestration_id text NOT NULL,
    target_orchestration_id text NOT NULL UNIQUE,
    source_test_plan_id text NOT NULL,
    target_test_plan_id text NOT NULL,
    stale_run_ids jsonb NOT NULL,
    stale_artifact_refs jsonb NOT NULL,
    stale_evidence_refs jsonb NOT NULL,
    stale_closure_result_ids jsonb NOT NULL,
    applied_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT test_case_revisions_fields_not_blank CHECK (
        btrim(revision_id) <> ''
        AND btrim(source_test_plan_id) <> ''
        AND btrim(target_test_plan_id) <> ''
        AND btrim(applied_by) <> ''
    ),
    CONSTRAINT test_case_revisions_arrays_valid CHECK (
        jsonb_typeof(stale_run_ids) = 'array'
        AND jsonb_typeof(stale_artifact_refs) = 'array'
        AND jsonb_typeof(stale_evidence_refs) = 'array'
        AND jsonb_typeof(stale_closure_result_ids) = 'array'
    ),
    CONSTRAINT test_case_revisions_artifact_fk FOREIGN KEY (revision_id)
        REFERENCES artifact_records(artifact_id),
    CONSTRAINT test_case_revisions_proposal_fk FOREIGN KEY (
        proposal_id, project_id
    ) REFERENCES test_case_change_proposals(proposal_id, project_id),
    CONSTRAINT test_case_revisions_request_fk FOREIGN KEY (
        change_request_id, project_id
    ) REFERENCES change_requests(change_request_id, project_id),
    CONSTRAINT test_case_revisions_source_fk FOREIGN KEY (
        source_orchestration_id, project_id
    ) REFERENCES change_orchestrations(orchestration_id, project_id),
    CONSTRAINT test_case_revisions_target_fk FOREIGN KEY (
        target_orchestration_id, project_id
    ) REFERENCES change_orchestrations(orchestration_id, project_id),
    CONSTRAINT test_case_revisions_scope_unique UNIQUE (revision_id, project_id)
);

CREATE INDEX test_case_change_proposals_request_idx
    ON test_case_change_proposals (
        project_id, change_request_id, created_at DESC, proposal_id DESC
    );

CREATE INDEX test_case_revisions_request_idx
    ON test_case_revisions (
        project_id, change_request_id, created_at DESC, revision_id DESC
    );
