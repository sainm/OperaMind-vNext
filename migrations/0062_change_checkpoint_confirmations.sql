CREATE TABLE change_checkpoint_confirmations (
    confirmation_id text PRIMARY KEY,
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    automation_run_id text NOT NULL,
    project_id text NOT NULL,
    checkpoint text NOT NULL,
    subject_digest text NOT NULL,
    decision text NOT NULL,
    surface text NOT NULL,
    actor text NOT NULL,
    note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT change_checkpoint_confirmations_fields_not_blank CHECK (
        btrim(confirmation_id) <> ''
        AND sequence > 0
        AND btrim(checkpoint) <> ''
        AND btrim(surface) <> ''
        AND btrim(actor) <> ''
        AND (note IS NULL OR btrim(note) <> '')
    ),
    CONSTRAINT change_checkpoint_confirmations_checkpoint_valid CHECK (
        checkpoint IN (
            'requirement',
            'rag_documents',
            'document_diff',
            'code_scope',
            'test_plan',
            'ui_test',
            'final_report'
        )
    ),
    CONSTRAINT change_checkpoint_confirmations_digest_sha256 CHECK (
        subject_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT change_checkpoint_confirmations_decision_valid CHECK (
        decision IN ('confirmed', 'rejected')
    ),
    CONSTRAINT change_checkpoint_confirmations_surface_valid CHECK (
        surface IN ('web', 'vscode_copilot')
    ),
    CONSTRAINT change_checkpoint_confirmations_run_fk FOREIGN KEY (
        automation_run_id, project_id
    ) REFERENCES change_automation_runs(automation_run_id, project_id)
);

CREATE INDEX change_checkpoint_confirmations_current_idx
    ON change_checkpoint_confirmations (
        project_id, automation_run_id, checkpoint, sequence DESC
    );
