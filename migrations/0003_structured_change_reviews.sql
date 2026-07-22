ALTER TABLE structured_changes
    ADD CONSTRAINT structured_changes_project_change_unique
    UNIQUE (project_id, structured_change_id);

CREATE TABLE structured_change_review_events (
    review_event_id text PRIMARY KEY,
    project_id text NOT NULL,
    structured_change_id text NOT NULL,
    previous_review_event_id text,
    previous_review_status text NOT NULL,
    decision text NOT NULL,
    reviewed_by text NOT NULL,
    reason text NOT NULL,
    review_sequence bigint GENERATED ALWAYS AS IDENTITY,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT structured_change_reviews_identity_not_blank CHECK (
        btrim(review_event_id) <> ''
        AND btrim(reviewed_by) <> ''
        AND btrim(reason) <> ''
    ),
    CONSTRAINT structured_change_reviews_previous_status_valid CHECK (
        previous_review_status IN ('needs_review', 'accepted', 'rejected')
    ),
    CONSTRAINT structured_change_reviews_decision_valid CHECK (
        decision IN ('accepted', 'rejected')
    ),
    CONSTRAINT structured_change_reviews_first_event_consistent CHECK (
        previous_review_event_id IS NOT NULL
        OR previous_review_status = 'needs_review'
    ),
    CONSTRAINT structured_change_reviews_change_fk FOREIGN KEY (
        project_id,
        structured_change_id
    ) REFERENCES structured_changes(project_id, structured_change_id),
    CONSTRAINT structured_change_reviews_chain_identity_unique UNIQUE (
        project_id,
        structured_change_id,
        review_event_id,
        decision
    ),
    CONSTRAINT structured_change_reviews_previous_event_fk FOREIGN KEY (
        project_id,
        structured_change_id,
        previous_review_event_id,
        previous_review_status
    ) REFERENCES structured_change_review_events(
        project_id,
        structured_change_id,
        review_event_id,
        decision
    ),
    CONSTRAINT structured_change_reviews_sequence_unique UNIQUE (review_sequence)
);

CREATE INDEX structured_change_reviews_change_latest_idx
    ON structured_change_review_events (
        project_id,
        structured_change_id,
        review_sequence DESC
    );
