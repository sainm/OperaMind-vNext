ALTER TABLE analysis_cases
    ADD CONSTRAINT analysis_cases_project_identity_unique
    UNIQUE (project_id, analysis_case_id);

ALTER TABLE artifact_records
    ADD CONSTRAINT artifact_records_project_id_unique
    UNIQUE (project_id, artifact_id);

ALTER TABLE search_index_builds
    ADD CONSTRAINT search_index_builds_project_id_unique
    UNIQUE (project_id, search_index_build_id);

CREATE TABLE document_ingestion_result_events (
    ingestion_result_event_id text PRIMARY KEY,
    project_id text NOT NULL,
    ingestion_batch_id text NOT NULL,
    analysis_case_id text NOT NULL,
    previous_event_id text,
    previous_status text,
    artifact_id text NOT NULL,
    search_index_build_id text,
    status text NOT NULL,
    event_sequence bigint GENERATED ALWAYS AS IDENTITY,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ingestion_result_events_identity_not_blank CHECK (
        btrim(ingestion_result_event_id) <> ''
        AND btrim(ingestion_batch_id) <> ''
        AND btrim(artifact_id) <> ''
    ),
    CONSTRAINT ingestion_result_events_status_valid CHECK (
        status IN (
            'ingesting',
            'indexing_rag',
            'ready_for_impact',
            'needs_review',
            'blocked',
            'failed'
        )
    ),
    CONSTRAINT ingestion_result_events_previous_consistent CHECK (
        (previous_event_id IS NULL AND previous_status IS NULL)
        OR (previous_event_id IS NOT NULL AND previous_status IS NOT NULL)
    ),
    CONSTRAINT ingestion_result_events_ready_has_build CHECK (
        status <> 'ready_for_impact' OR search_index_build_id IS NOT NULL
    ),
    CONSTRAINT ingestion_result_events_case_fk FOREIGN KEY (
        project_id,
        analysis_case_id
    ) REFERENCES analysis_cases(project_id, analysis_case_id),
    CONSTRAINT ingestion_result_events_artifact_fk FOREIGN KEY (
        project_id,
        artifact_id
    ) REFERENCES artifact_records(project_id, artifact_id),
    CONSTRAINT ingestion_result_events_build_fk FOREIGN KEY (
        project_id,
        search_index_build_id
    ) REFERENCES search_index_builds(project_id, search_index_build_id),
    CONSTRAINT ingestion_result_events_chain_identity_unique UNIQUE (
        project_id,
        ingestion_batch_id,
        ingestion_result_event_id,
        status
    ),
    CONSTRAINT ingestion_result_events_previous_fk FOREIGN KEY (
        project_id,
        ingestion_batch_id,
        previous_event_id,
        previous_status
    ) REFERENCES document_ingestion_result_events(
        project_id,
        ingestion_batch_id,
        ingestion_result_event_id,
        status
    ),
    CONSTRAINT ingestion_result_events_artifact_unique UNIQUE (artifact_id),
    CONSTRAINT ingestion_result_events_sequence_unique UNIQUE (event_sequence)
);

CREATE UNIQUE INDEX ingestion_result_events_first_unique
    ON document_ingestion_result_events (project_id, ingestion_batch_id)
    WHERE previous_event_id IS NULL;

CREATE UNIQUE INDEX ingestion_result_events_successor_unique
    ON document_ingestion_result_events (
        project_id,
        ingestion_batch_id,
        previous_event_id
    )
    WHERE previous_event_id IS NOT NULL;

CREATE INDEX ingestion_result_events_latest_idx
    ON document_ingestion_result_events (
        project_id,
        ingestion_batch_id,
        event_sequence DESC
    );
