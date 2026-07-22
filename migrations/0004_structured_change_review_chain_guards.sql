CREATE UNIQUE INDEX structured_change_reviews_first_event_unique
    ON structured_change_review_events (project_id, structured_change_id)
    WHERE previous_review_event_id IS NULL;

CREATE UNIQUE INDEX structured_change_reviews_successor_unique
    ON structured_change_review_events (
        project_id,
        structured_change_id,
        previous_review_event_id
    )
    WHERE previous_review_event_id IS NOT NULL;
