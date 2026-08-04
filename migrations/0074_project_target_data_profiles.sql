ALTER TABLE project_workspaces
    ADD COLUMN target_data_connection_alias text,
    ADD CONSTRAINT project_workspaces_target_data_alias_not_blank CHECK (
        target_data_connection_alias IS NULL
        OR btrim(target_data_connection_alias) <> ''
    );

CREATE TABLE project_target_data_profiles (
    project_id text NOT NULL REFERENCES projects(project_id),
    connection_alias text NOT NULL,
    dialect text NOT NULL,
    transaction_policy text NOT NULL,
    reviewed_by text NOT NULL,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, connection_alias),
    CONSTRAINT project_target_data_profiles_identity_not_blank CHECK (
        btrim(connection_alias) <> '' AND btrim(reviewed_by) <> ''
    ),
    CONSTRAINT project_target_data_profiles_dialect_valid CHECK (
        dialect = 'postgresql'
    ),
    CONSTRAINT project_target_data_profiles_transaction_policy_valid CHECK (
        transaction_policy = 'per_binding_transaction'
    )
);

CREATE TABLE project_target_data_query_bindings (
    project_id text NOT NULL,
    connection_alias text NOT NULL,
    query_binding_id text NOT NULL,
    operation text NOT NULL,
    statement_text text NOT NULL,
    target_schema text NOT NULL,
    target_table text NOT NULL,
    parameter_columns jsonb NOT NULL,
    input_constraints jsonb NOT NULL,
    read_after_write_statement text NOT NULL,
    read_assertion jsonb NOT NULL,
    cleanup_binding_id text,
    idempotency_policy text NOT NULL,
    reviewed_by text NOT NULL,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, query_binding_id),
    FOREIGN KEY (project_id, connection_alias)
        REFERENCES project_target_data_profiles(project_id, connection_alias)
        ON DELETE CASCADE,
    CONSTRAINT project_target_data_query_bindings_identity_not_blank CHECK (
        btrim(connection_alias) <> ''
        AND btrim(query_binding_id) <> ''
        AND btrim(statement_text) <> ''
        AND btrim(target_schema) <> ''
        AND btrim(target_table) <> ''
        AND btrim(read_after_write_statement) <> ''
        AND btrim(reviewed_by) <> ''
    ),
    CONSTRAINT project_target_data_query_bindings_operation_valid CHECK (
        operation IN ('write', 'read', 'cleanup')
    ),
    CONSTRAINT project_target_data_query_bindings_json_objects CHECK (
        jsonb_typeof(parameter_columns) = 'object'
        AND jsonb_typeof(input_constraints) = 'object'
        AND jsonb_typeof(read_assertion) = 'object'
    ),
    CONSTRAINT project_target_data_query_bindings_idempotency_valid CHECK (
        idempotency_policy IN ('natural_key', 'upsert', 'delete_then_insert', 'read_only')
    ),
    CONSTRAINT project_target_data_query_bindings_cleanup_consistent CHECK (
        (operation = 'write' AND cleanup_binding_id IS NOT NULL
            AND btrim(cleanup_binding_id) <> '')
        OR (operation <> 'write' AND cleanup_binding_id IS NULL)
    )
);

CREATE INDEX project_target_data_query_bindings_profile_idx
    ON project_target_data_query_bindings (project_id, connection_alias, query_binding_id);
