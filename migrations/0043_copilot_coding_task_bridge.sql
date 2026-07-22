CREATE TABLE copilot_coding_tasks (
    coding_task_id text PRIMARY KEY,
    project_id text NOT NULL,
    change_request_id text NOT NULL,
    analysis_case_id text NOT NULL,
    repository_id text NOT NULL,
    edit_packet_id text NOT NULL,
    approval_grant_id text NOT NULL,
    base_repository_revision text NOT NULL,
    execution_mode text NOT NULL,
    provider_route text NOT NULL,
    provider_id text NOT NULL,
    workspace_root text NOT NULL,
    state text NOT NULL,
    payload_digest text NOT NULL,
    created_by text NOT NULL,
    claimed_by text,
    accepted_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    claimed_at timestamptz,
    accepted_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT copilot_coding_tasks_fields_not_blank CHECK (
        btrim(coding_task_id) <> ''
        AND btrim(base_repository_revision) <> ''
        AND btrim(provider_id) <> ''
        AND btrim(workspace_root) <> ''
        AND btrim(created_by) <> ''
    ),
    CONSTRAINT copilot_coding_tasks_execution_mode_valid CHECK (
        execution_mode = 'copilot_coding_plan'
    ),
    CONSTRAINT copilot_coding_tasks_provider_route_valid CHECK (
        provider_route IN ('local_bridge', 'api_provider')
    ),
    CONSTRAINT copilot_coding_tasks_state_valid CHECK (
        state IN (
            'pending_confirmation', 'accepted', 'in_progress',
            'completed', 'failed', 'reanalysis_required', 'cancelled'
        )
    ),
    CONSTRAINT copilot_coding_tasks_digest_sha256 CHECK (
        payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT copilot_coding_tasks_claim_consistent CHECK (
        (claimed_by IS NULL AND claimed_at IS NULL)
        OR (btrim(claimed_by) <> '' AND claimed_at IS NOT NULL)
    ),
    CONSTRAINT copilot_coding_tasks_accept_consistent CHECK (
        (accepted_by IS NULL AND accepted_at IS NULL)
        OR (btrim(accepted_by) <> '' AND accepted_at IS NOT NULL)
    ),
    CONSTRAINT copilot_coding_tasks_artifact_fk FOREIGN KEY (coding_task_id)
        REFERENCES artifact_records(artifact_id),
    CONSTRAINT copilot_coding_tasks_request_fk FOREIGN KEY (
        change_request_id, project_id
    ) REFERENCES change_requests(change_request_id, project_id),
    CONSTRAINT copilot_coding_tasks_case_fk FOREIGN KEY (
        analysis_case_id, project_id
    ) REFERENCES analysis_cases(analysis_case_id, project_id),
    CONSTRAINT copilot_coding_tasks_repository_fk FOREIGN KEY (
        repository_id, project_id
    ) REFERENCES repositories(repository_id, project_id),
    CONSTRAINT copilot_coding_tasks_packet_fk FOREIGN KEY (
        edit_packet_id, project_id
    ) REFERENCES edit_packets(edit_packet_id, project_id),
    CONSTRAINT copilot_coding_tasks_grant_fk FOREIGN KEY (
        approval_grant_id, project_id
    ) REFERENCES approval_grants(approval_grant_id, project_id),
    CONSTRAINT copilot_coding_tasks_scope_identity_unique UNIQUE (
        coding_task_id, project_id
    )
);

CREATE UNIQUE INDEX copilot_coding_tasks_active_packet_unique
    ON copilot_coding_tasks (project_id, edit_packet_id)
    WHERE state IN ('pending_confirmation', 'accepted', 'in_progress');

CREATE INDEX copilot_coding_tasks_bridge_queue_idx
    ON copilot_coding_tasks (
        provider_route, workspace_root, state, created_at, coding_task_id
    );

CREATE TABLE copilot_coding_task_events (
    coding_task_event_id text PRIMARY KEY,
    event_sequence bigint GENERATED ALWAYS AS IDENTITY,
    coding_task_id text NOT NULL,
    project_id text NOT NULL,
    event_type text NOT NULL,
    actor text NOT NULL,
    idempotency_key text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT copilot_coding_task_events_fields_not_blank CHECK (
        btrim(coding_task_event_id) <> ''
        AND btrim(actor) <> ''
        AND btrim(idempotency_key) <> ''
    ),
    CONSTRAINT copilot_coding_task_events_type_valid CHECK (
        event_type IN (
            'published', 'claimed', 'accepted', 'context_loaded',
            'command_recorded', 'diff_recorded', 'result_recorded',
            'failed', 'reanalysis_required', 'cancelled'
        )
    ),
    CONSTRAINT copilot_coding_task_events_payload_object CHECK (
        jsonb_typeof(payload) = 'object'
    ),
    CONSTRAINT copilot_coding_task_events_task_fk FOREIGN KEY (
        coding_task_id, project_id
    ) REFERENCES copilot_coding_tasks(coding_task_id, project_id),
    CONSTRAINT copilot_coding_task_events_idempotent UNIQUE (
        coding_task_id, idempotency_key
    ),
    CONSTRAINT copilot_coding_task_events_sequence_unique UNIQUE (
        coding_task_id, event_sequence
    )
);

CREATE INDEX copilot_coding_task_events_timeline_idx
    ON copilot_coding_task_events (
        project_id, coding_task_id, event_sequence
    );

CREATE TABLE copilot_coding_task_commands (
    coding_task_id text NOT NULL,
    project_id text NOT NULL,
    command_execution_id text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (coding_task_id, command_execution_id),
    CONSTRAINT copilot_coding_task_commands_task_fk FOREIGN KEY (
        coding_task_id, project_id
    ) REFERENCES copilot_coding_tasks(coding_task_id, project_id),
    CONSTRAINT copilot_coding_task_commands_execution_fk FOREIGN KEY (
        command_execution_id, project_id
    ) REFERENCES command_execution_requests(command_execution_id, project_id),
    CONSTRAINT copilot_coding_task_commands_execution_unique UNIQUE (
        command_execution_id
    )
);

CREATE TABLE copilot_coding_task_edit_results (
    coding_task_id text NOT NULL,
    project_id text NOT NULL,
    edit_result_id text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (coding_task_id, edit_result_id),
    CONSTRAINT copilot_coding_task_edit_results_task_fk FOREIGN KEY (
        coding_task_id, project_id
    ) REFERENCES copilot_coding_tasks(coding_task_id, project_id),
    CONSTRAINT copilot_coding_task_edit_results_result_fk FOREIGN KEY (
        edit_result_id, project_id
    ) REFERENCES edit_results(edit_result_id, project_id),
    CONSTRAINT copilot_coding_task_edit_results_result_unique UNIQUE (
        edit_result_id
    )
);
