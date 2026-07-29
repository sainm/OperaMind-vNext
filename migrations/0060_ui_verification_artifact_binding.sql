ALTER TABLE change_closure_results
    DROP CONSTRAINT change_closure_results_ui_fk;

ALTER TABLE change_closure_results
    ADD CONSTRAINT change_closure_results_ui_fk FOREIGN KEY (
        project_id, ui_verification_result_id
    ) REFERENCES artifact_records(project_id, artifact_id);
