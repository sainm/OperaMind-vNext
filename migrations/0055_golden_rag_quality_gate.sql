ALTER TABLE search_index_builds
    ADD CONSTRAINT search_index_builds_quality_scope_unique UNIQUE (
        search_index_build_id, project_id,
        document_snapshot_id, embedding_profile_version_id
    );

CREATE TABLE golden_rag_quality_reports (
    report_id text PRIMARY KEY REFERENCES artifact_records(artifact_id),
    case_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_version text NOT NULL,
    project_id text NOT NULL,
    document_snapshot_id text NOT NULL,
    embedding_profile_version_id text NOT NULL,
    embedding_profile_binding_key text NOT NULL,
    search_index_build_id text NOT NULL,
    ranking_policy_version text NOT NULL,
    query_plan_version text NOT NULL,
    expectation_digest text NOT NULL,
    status text NOT NULL,
    recall_at_5 double precision,
    recall_at_10 double precision,
    mrr double precision,
    irrelevant_rate double precision,
    cross_project_leaks integer,
    threshold_failures jsonb NOT NULL,
    failure_reasons jsonb NOT NULL,
    report_digest text NOT NULL,
    created_by text NOT NULL,
    publication_sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT golden_rag_quality_reports_fields_not_blank CHECK (
        btrim(report_id) <> ''
        AND btrim(case_id) <> ''
        AND btrim(dataset_id) <> ''
        AND btrim(dataset_version) <> ''
        AND btrim(embedding_profile_binding_key) <> ''
        AND btrim(ranking_policy_version) <> ''
        AND btrim(query_plan_version) <> ''
        AND btrim(created_by) <> ''
    ),
    CONSTRAINT golden_rag_quality_reports_digests_sha256 CHECK (
        expectation_digest ~ '^[0-9a-f]{64}$'
        AND report_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT golden_rag_quality_reports_status_valid CHECK (
        status IN ('passed', 'failed', 'blocked')
    ),
    CONSTRAINT golden_rag_quality_reports_arrays_valid CHECK (
        jsonb_typeof(threshold_failures) = 'array'
        AND jsonb_typeof(failure_reasons) = 'array'
    ),
    CONSTRAINT golden_rag_quality_reports_metrics_consistent CHECK (
        (
            status IN ('passed', 'failed')
            AND recall_at_5 BETWEEN 0 AND 1
            AND recall_at_10 BETWEEN 0 AND 1
            AND mrr BETWEEN 0 AND 1
            AND irrelevant_rate BETWEEN 0 AND 1
            AND cross_project_leaks >= 0
        )
        OR (
            status = 'blocked'
            AND recall_at_5 IS NULL
            AND recall_at_10 IS NULL
            AND mrr IS NULL
            AND irrelevant_rate IS NULL
            AND cross_project_leaks IS NULL
        )
    ),
    CONSTRAINT golden_rag_quality_reports_outcome_consistent CHECK (
        (
            status = 'passed'
            AND jsonb_array_length(threshold_failures) = 0
            AND jsonb_array_length(failure_reasons) = 0
        )
        OR (
            status = 'failed'
            AND jsonb_array_length(threshold_failures) > 0
            AND jsonb_array_length(failure_reasons) > 0
        )
        OR (
            status = 'blocked'
            AND jsonb_array_length(threshold_failures) = 0
            AND jsonb_array_length(failure_reasons) > 0
        )
    ),
    CONSTRAINT golden_rag_quality_reports_search_scope_fk FOREIGN KEY (
        search_index_build_id, project_id,
        document_snapshot_id, embedding_profile_version_id
    ) REFERENCES search_index_builds (
        search_index_build_id, project_id,
        document_snapshot_id, embedding_profile_version_id
    ),
    CONSTRAINT golden_rag_quality_reports_scope_unique UNIQUE (
        report_id, project_id
    )
);

CREATE TABLE golden_rag_query_results (
    report_id text NOT NULL,
    project_id text NOT NULL,
    query_purpose text NOT NULL,
    query_text_digest text NOT NULL,
    candidates jsonb NOT NULL,
    failure_reasons jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (report_id, query_purpose),
    CONSTRAINT golden_rag_query_results_purpose_valid CHECK (
        query_purpose IN ('business_behavior', 'precise_anchor', 'acceptance_criteria')
    ),
    CONSTRAINT golden_rag_query_results_digest_sha256 CHECK (
        query_text_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT golden_rag_query_results_payload_valid CHECK (
        jsonb_typeof(candidates) = 'array'
        AND jsonb_typeof(failure_reasons) = 'array'
    ),
    CONSTRAINT golden_rag_query_results_report_fk FOREIGN KEY (
        report_id, project_id
    ) REFERENCES golden_rag_quality_reports(report_id, project_id)
);

CREATE INDEX golden_rag_quality_reports_scope_latest_idx
    ON golden_rag_quality_reports (
        project_id, document_snapshot_id, embedding_profile_version_id,
        search_index_build_id, publication_sequence DESC
    );
