ALTER TABLE command_execution_results
    ADD COLUMN coverage_report_format text,
    ADD COLUMN coverage_report_path text,
    ADD COLUMN coverage_report_digest text,
    ADD CONSTRAINT command_execution_results_coverage_consistent CHECK (
        (
            coverage_report_format IS NULL
            AND coverage_report_path IS NULL
            AND coverage_report_digest IS NULL
        )
        OR (
            status = 'passed'
            AND btrim(coverage_report_format) <> ''
            AND btrim(coverage_report_path) <> ''
            AND coverage_report_digest ~ '^[0-9a-f]{64}$'
        )
    );
