CREATE TABLE projects (
    project_id text PRIMARY KEY,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT projects_name_not_blank CHECK (btrim(name) <> '')
);

CREATE TABLE repositories (
    repository_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    remote_url text NOT NULL,
    workspace_root text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT repositories_remote_url_not_blank CHECK (btrim(remote_url) <> ''),
    CONSTRAINT repositories_project_remote_unique UNIQUE (project_id, remote_url)
);

CREATE TABLE repository_revisions (
    repository_revision_id text PRIMARY KEY,
    repository_id text NOT NULL REFERENCES repositories(repository_id),
    commit_sha text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT repository_revisions_commit_not_blank CHECK (btrim(commit_sha) <> ''),
    CONSTRAINT repository_revisions_repository_commit_unique UNIQUE (repository_id, commit_sha)
);

CREATE TABLE analysis_cases (
    analysis_case_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    repository_revision_id text NOT NULL REFERENCES repository_revisions(repository_revision_id),
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT analysis_cases_status_valid CHECK (
        status IN (
            'ingesting',
            'indexing_rag',
            'ready_for_impact',
            'analyzing',
            'awaiting_confirmation',
            'editing',
            'verifying_ui',
            'passed',
            'failed',
            'reanalysis_required'
        )
    )
);

-- Artifact payloads are immutable exchange records. Later phase migrations add
-- normalized canonical tables; this table does not replace those query models.
CREATE TABLE artifact_records (
    artifact_id text PRIMARY KEY,
    artifact_type text NOT NULL,
    schema_version text NOT NULL,
    project_id text NOT NULL REFERENCES projects(project_id),
    analysis_case_id text REFERENCES analysis_cases(analysis_case_id),
    payload jsonb NOT NULL,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT artifact_records_type_not_blank CHECK (btrim(artifact_type) <> ''),
    CONSTRAINT artifact_records_version_not_blank CHECK (btrim(schema_version) <> ''),
    CONSTRAINT artifact_records_digest_not_blank CHECK (btrim(payload_digest) <> ''),
    CONSTRAINT artifact_records_envelope_matches_payload CHECK (
        payload ->> 'artifact_type' = artifact_type
        AND payload ->> 'schema_version' = schema_version
    ),
    CONSTRAINT artifact_records_identity_unique UNIQUE (
        project_id,
        artifact_type,
        artifact_id
    )
);

CREATE INDEX artifact_records_analysis_case_idx
    ON artifact_records (analysis_case_id, artifact_type, created_at);
