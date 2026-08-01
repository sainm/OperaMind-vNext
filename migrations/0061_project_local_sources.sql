CREATE TABLE project_workspaces (
    project_id text PRIMARY KEY REFERENCES projects(project_id),
    workspace_root text NOT NULL,
    source_control_kind text NOT NULL,
    configured_by text NOT NULL,
    configured_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT project_workspaces_root_not_blank CHECK (btrim(workspace_root) <> ''),
    CONSTRAINT project_workspaces_source_control_valid CHECK (
        source_control_kind IN ('git', 'local_files')
    ),
    CONSTRAINT project_workspaces_actor_not_blank CHECK (btrim(configured_by) <> '')
);

CREATE TABLE project_document_roots (
    document_root_id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id),
    root_path text NOT NULL,
    position integer NOT NULL,
    configured_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT project_document_roots_path_not_blank CHECK (btrim(root_path) <> ''),
    CONSTRAINT project_document_roots_position_nonnegative CHECK (position >= 0),
    CONSTRAINT project_document_roots_project_path_unique UNIQUE (project_id, root_path),
    CONSTRAINT project_document_roots_project_position_unique UNIQUE (project_id, position)
);

-- Preserve the Workspace that older Git-backed projects already registered.
-- Document roots have no legacy equivalent and therefore remain empty until
-- the project is initialized through the Web screen.
INSERT INTO project_workspaces (
    project_id, workspace_root, source_control_kind, configured_by
)
SELECT DISTINCT ON (repository.project_id)
    repository.project_id,
    repository.workspace_root,
    'git',
    'migration:0061'
FROM repositories AS repository
WHERE repository.workspace_root IS NOT NULL
ORDER BY repository.project_id, repository.created_at, repository.repository_id
ON CONFLICT (project_id) DO NOTHING;
