ALTER TABLE test_case_revisions
    ADD COLUMN revision_kind text NOT NULL DEFAULT 'modification',
    ADD COLUMN undo_of_revision_id text,
    ADD CONSTRAINT test_case_revisions_kind_valid CHECK (
        revision_kind IN ('modification', 'undo')
    ),
    ADD CONSTRAINT test_case_revisions_undo_consistent CHECK (
        (revision_kind = 'undo' AND undo_of_revision_id IS NOT NULL)
        OR (revision_kind = 'modification' AND undo_of_revision_id IS NULL)
    ),
    ADD CONSTRAINT test_case_revisions_undo_source_fk FOREIGN KEY (
        undo_of_revision_id, project_id
    ) REFERENCES test_case_revisions(revision_id, project_id);

CREATE UNIQUE INDEX test_case_revisions_one_undo_idx
    ON test_case_revisions (undo_of_revision_id)
    WHERE undo_of_revision_id IS NOT NULL;
