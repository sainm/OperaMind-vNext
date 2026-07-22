CREATE TABLE ui_preflight_attempts (
    ui_preflight_attempt_id text PRIMARY KEY,
    ui_execution_plan_id text NOT NULL,
    project_id text NOT NULL,
    status text NOT NULL,
    blocking_reasons jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_preflight_attempts_fields_not_blank CHECK (
        btrim(ui_preflight_attempt_id) <> ''
    ),
    CONSTRAINT ui_preflight_attempts_status_valid CHECK (
        status IN ('passed', 'blocked')
    ),
    CONSTRAINT ui_preflight_attempts_reasons_valid CHECK (
        jsonb_typeof(blocking_reasons) = 'array'
        AND (
            (status = 'passed' AND jsonb_array_length(blocking_reasons) = 0)
            OR (status = 'blocked' AND jsonb_array_length(blocking_reasons) > 0)
        )
    ),
    CONSTRAINT ui_preflight_attempts_plan_fk FOREIGN KEY (
        ui_execution_plan_id, project_id
    ) REFERENCES ui_execution_plans(ui_execution_plan_id, project_id),
    CONSTRAINT ui_preflight_attempts_scope_identity_unique UNIQUE (
        ui_preflight_attempt_id, project_id
    )
);

ALTER TABLE ui_preflight_checks
    ADD COLUMN ui_preflight_attempt_id text;

INSERT INTO ui_preflight_attempts (
    ui_preflight_attempt_id,
    ui_execution_plan_id,
    project_id,
    status,
    blocking_reasons
)
SELECT
    'preflight-attempt-legacy-' || md5(ui_execution_plan_id),
    ui_execution_plan_id,
    project_id,
    CASE WHEN bool_and(status = 'passed') THEN 'passed' ELSE 'blocked' END,
    COALESCE(
        jsonb_agg(check_type || ':' || status || ':' || COALESCE(reason, ''))
            FILTER (WHERE status <> 'passed'),
        '[]'::jsonb
    )
FROM ui_preflight_checks
GROUP BY ui_execution_plan_id, project_id;

UPDATE ui_preflight_checks
SET ui_preflight_attempt_id = 'preflight-attempt-legacy-' || md5(ui_execution_plan_id);

ALTER TABLE ui_preflight_checks
    ALTER COLUMN ui_preflight_attempt_id SET NOT NULL,
    DROP CONSTRAINT ui_preflight_checks_type_unique,
    ADD CONSTRAINT ui_preflight_checks_attempt_fk FOREIGN KEY (
        ui_preflight_attempt_id, project_id
    ) REFERENCES ui_preflight_attempts(ui_preflight_attempt_id, project_id),
    ADD CONSTRAINT ui_preflight_checks_attempt_type_unique UNIQUE (
        ui_preflight_attempt_id, check_type
    );

CREATE INDEX ui_preflight_attempts_plan_idx
    ON ui_preflight_attempts (project_id, ui_execution_plan_id, created_at DESC);
