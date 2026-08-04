ALTER TABLE test_data_execution_evidence
    DROP CONSTRAINT test_data_execution_evidence_type_valid,
    ADD CONSTRAINT test_data_execution_evidence_type_valid CHECK (
        evidence_type IN (
            'fixture', 'request', 'response', 'sql', 'screenshot',
            'assertion', 'step_log', 'cleanup', 'data_binding',
            'data_coverage'
        )
    );
