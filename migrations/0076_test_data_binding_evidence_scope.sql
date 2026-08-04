ALTER TABLE test_data_execution_evidence
    ADD CONSTRAINT test_data_execution_evidence_binding_scope_unique UNIQUE (
        evidence_ref, run_id, project_id, flow_id, phase, step_id, content_digest
    );

ALTER TABLE test_data_identity_bindings
    ADD CONSTRAINT test_data_identity_bindings_evidence_scope_fk FOREIGN KEY (
        evidence_ref, run_id, project_id, source_flow_id, source_phase,
        source_step_id, content_digest
    ) REFERENCES test_data_execution_evidence (
        evidence_ref, run_id, project_id, flow_id, phase, step_id, content_digest
    );
