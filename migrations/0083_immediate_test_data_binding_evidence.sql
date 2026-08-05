ALTER TABLE test_data_execution_evidence
    DROP CONSTRAINT test_data_execution_evidence_binding_fk,
    ADD CONSTRAINT test_data_execution_evidence_binding_fk FOREIGN KEY (
        test_data_binding_ref, run_id, project_id
    ) REFERENCES test_data_identity_bindings (
        binding_id, run_id, project_id
    ) NOT DEFERRABLE;

COMMENT ON CONSTRAINT test_data_execution_evidence_binding_fk
    ON test_data_execution_evidence IS
    'Binding-referencing Evidence is inserted after its frozen Binding; immediate validation avoids pending trigger events';
