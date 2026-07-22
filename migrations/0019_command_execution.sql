ALTER TABLE profile_versions
    DROP CONSTRAINT profile_versions_type_valid;

ALTER TABLE profile_versions
    ADD CONSTRAINT profile_versions_type_valid CHECK (
        profile_type IN (
            'EmbeddingProfile',
            'DocumentConventionProfile',
            'DocumentRelationProfile',
            'CodeFrameworkProfile',
            'CommandExecutionProfile'
        )
    );

ALTER TABLE approval_grants
    ADD COLUMN command_profile_version_id text,
    ADD CONSTRAINT approval_grants_command_profile_fk FOREIGN KEY (
        command_profile_version_id
    ) REFERENCES profile_versions(profile_version_id);

CREATE TABLE command_execution_requests (
    command_execution_id text PRIMARY KEY,
    approval_grant_id text NOT NULL,
    project_id text NOT NULL,
    analysis_case_id text NOT NULL,
    edit_packet_id text NOT NULL,
    repository_id text NOT NULL,
    command_profile_version_id text NOT NULL,
    command_ref text NOT NULL,
    base_repository_revision text NOT NULL,
    remote_url text NOT NULL,
    workspace_root text NOT NULL,
    template_digest text NOT NULL,
    request_digest text NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT command_execution_requests_fields_not_blank CHECK (
        btrim(command_execution_id) <> ''
        AND btrim(command_ref) <> ''
        AND btrim(base_repository_revision) <> ''
        AND btrim(remote_url) <> ''
        AND btrim(workspace_root) <> ''
    ),
    CONSTRAINT command_execution_requests_digests_sha256 CHECK (
        template_digest ~ '^[0-9a-f]{64}$'
        AND request_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT command_execution_requests_grant_fk FOREIGN KEY (
        approval_grant_id, project_id
    ) REFERENCES approval_grants(approval_grant_id, project_id),
    CONSTRAINT command_execution_requests_case_fk FOREIGN KEY (
        analysis_case_id, project_id
    ) REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT command_execution_requests_packet_fk FOREIGN KEY (
        edit_packet_id, project_id
    ) REFERENCES edit_packets(edit_packet_id, project_id),
    CONSTRAINT command_execution_requests_repository_fk FOREIGN KEY (
        repository_id
    ) REFERENCES repositories(repository_id),
    CONSTRAINT command_execution_requests_profile_fk FOREIGN KEY (
        command_profile_version_id
    ) REFERENCES profile_versions(profile_version_id),
    CONSTRAINT command_execution_requests_scope_identity_unique UNIQUE (
        command_execution_id, project_id
    )
);

CREATE TABLE command_execution_results (
    command_execution_id text PRIMARY KEY,
    project_id text NOT NULL,
    status text NOT NULL,
    exit_code integer,
    executable_path text NOT NULL,
    working_directory text NOT NULL,
    stdout_digest text NOT NULL,
    stderr_digest text NOT NULL,
    stdout_bytes integer NOT NULL,
    stderr_bytes integer NOT NULL,
    output_truncated boolean NOT NULL,
    result_digest text NOT NULL,
    started_at timestamptz NOT NULL,
    completed_at timestamptz NOT NULL,
    CONSTRAINT command_execution_results_fields_not_blank CHECK (
        btrim(executable_path) <> '' AND btrim(working_directory) <> ''
    ),
    CONSTRAINT command_execution_results_status_valid CHECK (
        status IN ('passed', 'failed', 'timed_out', 'launch_failed')
    ),
    CONSTRAINT command_execution_results_exit_consistent CHECK (
        (status IN ('passed', 'failed') AND exit_code IS NOT NULL)
        OR (status IN ('timed_out', 'launch_failed') AND exit_code IS NULL)
    ),
    CONSTRAINT command_execution_results_digests_sha256 CHECK (
        stdout_digest ~ '^[0-9a-f]{64}$'
        AND stderr_digest ~ '^[0-9a-f]{64}$'
        AND result_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT command_execution_results_counts_valid CHECK (
        stdout_bytes >= 0 AND stderr_bytes >= 0 AND completed_at >= started_at
    ),
    CONSTRAINT command_execution_results_request_fk FOREIGN KEY (
        command_execution_id, project_id
    ) REFERENCES command_execution_requests(command_execution_id, project_id)
);

CREATE INDEX command_execution_requests_grant_idx
    ON command_execution_requests (project_id, approval_grant_id, requested_at DESC);
