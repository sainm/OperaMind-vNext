ALTER TABLE edit_results
    ADD COLUMN changed_line_coverage jsonb,
    ADD COLUMN changed_line_coverage_status text;

UPDATE edit_results
SET changed_line_coverage_status = CASE
        WHEN validation_mode = 'working' THEN 'not_required'
        ELSE 'missing'
    END,
    changed_line_coverage = jsonb_build_object(
        'artifact_type', 'ChangedLineCoverageReport',
        'schema_version', 'v1',
        'changed_line_coverage_report_id', 'changed-line-coverage-backfill-' || edit_result_id,
        'edit_result_id', edit_result_id,
        'project_id', project_id,
        'base_repository_revision', base_repository_revision,
        'result_repository_revision', COALESCE(result_repository_revision, base_repository_revision),
        'minimum_coverage_percent', 80,
        'changed_line_count', 0,
        'covered_changed_line_count', 0,
        'coverage_percent', 0,
        'files', '[]'::jsonb,
        'evidence_refs', '[]'::jsonb,
        'status', CASE WHEN validation_mode = 'working' THEN 'not_required' ELSE 'missing' END,
        'blocking_reasons', CASE
            WHEN validation_mode = 'working' THEN '[]'::jsonb
            ELSE '["Changed-line coverage evidence is missing"]'::jsonb
        END
    );

ALTER TABLE edit_results
    ALTER COLUMN changed_line_coverage SET NOT NULL,
    ALTER COLUMN changed_line_coverage_status SET NOT NULL,
    ADD CONSTRAINT edit_results_changed_line_coverage_status_valid CHECK (
        changed_line_coverage_status IN ('passed', 'failed', 'missing', 'not_required')
    ),
    ADD CONSTRAINT edit_results_changed_line_coverage_valid CHECK (
        jsonb_typeof(changed_line_coverage) = 'object'
        AND changed_line_coverage ->> 'artifact_type' = 'ChangedLineCoverageReport'
        AND changed_line_coverage ->> 'edit_result_id' = edit_result_id
        AND changed_line_coverage ->> 'project_id' = project_id
        AND changed_line_coverage ->> 'status' = changed_line_coverage_status
        AND jsonb_typeof(changed_line_coverage -> 'minimum_coverage_percent') = 'number'
        AND (changed_line_coverage ->> 'minimum_coverage_percent')::numeric BETWEEN 80 AND 100
        AND jsonb_typeof(changed_line_coverage -> 'files') = 'array'
        AND jsonb_typeof(changed_line_coverage -> 'evidence_refs') = 'array'
        AND jsonb_typeof(changed_line_coverage -> 'blocking_reasons') = 'array'
    );
