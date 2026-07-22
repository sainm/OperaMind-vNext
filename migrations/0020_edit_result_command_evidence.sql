CREATE TABLE edit_result_command_executions (
    edit_result_id text NOT NULL,
    command_execution_id text NOT NULL,
    project_id text NOT NULL,
    linked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (edit_result_id, command_execution_id),
    CONSTRAINT edit_result_command_executions_edit_result_fk FOREIGN KEY (
        edit_result_id, project_id
    ) REFERENCES edit_results(edit_result_id, project_id),
    CONSTRAINT edit_result_command_executions_request_scope_fk FOREIGN KEY (
        command_execution_id, project_id
    ) REFERENCES command_execution_requests(command_execution_id, project_id),
    CONSTRAINT edit_result_command_executions_result_fk FOREIGN KEY (
        command_execution_id
    ) REFERENCES command_execution_results(command_execution_id)
);

CREATE INDEX edit_result_command_executions_command_idx
    ON edit_result_command_executions (project_id, command_execution_id);
