ALTER TABLE existing_test_data_registrations
    ADD COLUMN change_request_id text,
    ADD CONSTRAINT existing_test_data_registrations_change_scope_fk FOREIGN KEY (
        change_request_id, project_id
    ) REFERENCES change_requests(change_request_id, project_id);

CREATE INDEX existing_test_data_registrations_change_scope_idx
    ON existing_test_data_registrations (
        project_id, change_request_id, requested_at DESC, registration_id DESC
    );

COMMENT ON COLUMN existing_test_data_registrations.change_request_id IS
    'Change Request whose reviewed TestPlan selected this existing target record; NULL is legacy history';
