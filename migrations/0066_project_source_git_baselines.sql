CREATE TABLE project_source_git_baselines (
    source_binding_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    source_kind text NOT NULL,
    configured_root text NOT NULL,
    repository_root text NOT NULL,
    repository_identity text NOT NULL,
    baseline_revision text NOT NULL,
    management_kind text NOT NULL,
    position integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT project_source_git_baselines_fields_not_blank CHECK (
        btrim(source_binding_id) <> ''
        AND btrim(configured_root) <> ''
        AND btrim(repository_root) <> ''
        AND btrim(repository_identity) <> ''
        AND btrim(baseline_revision) <> ''
    ),
    CONSTRAINT project_source_git_baselines_kind_valid CHECK (
        source_kind IN ('code', 'document')
    ),
    CONSTRAINT project_source_git_baselines_management_valid CHECK (
        management_kind IN ('existing_git', 'operamind_local_git')
    ),
    CONSTRAINT project_source_git_baselines_position_nonnegative CHECK (position >= 0),
    CONSTRAINT project_source_git_baselines_project_root_unique UNIQUE (
        project_id, source_kind, configured_root
    ),
    CONSTRAINT project_source_git_baselines_project_position_unique UNIQUE (
        project_id, source_kind, position
    )
);

CREATE INDEX project_source_git_baselines_repository_idx
    ON project_source_git_baselines (project_id, repository_root, source_kind, position);
