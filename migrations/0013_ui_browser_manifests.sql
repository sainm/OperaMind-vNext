ALTER TABLE ui_execution_plan_scenarios
    ADD CONSTRAINT ui_execution_plan_scenarios_exact_version_unique UNIQUE (
        ui_execution_plan_id, project_id, scenario_id, scenario_version_id
    );

CREATE TABLE ui_browser_manifests (
    browser_manifest_id text PRIMARY KEY,
    ui_execution_plan_id text NOT NULL,
    project_id text NOT NULL,
    browser_name text NOT NULL,
    browser_channel text,
    headless boolean NOT NULL,
    viewport_width integer NOT NULL,
    viewport_height integer NOT NULL,
    review_status text NOT NULL,
    reviewed_by text,
    payload_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_browser_manifests_fields_not_blank CHECK (
        btrim(browser_manifest_id) <> ''
        AND (browser_channel IS NULL OR btrim(browser_channel) <> '')
    ),
    CONSTRAINT ui_browser_manifests_browser_valid CHECK (
        browser_name IN ('chromium', 'firefox', 'webkit')
        AND (
            browser_channel IS NULL
            OR (browser_name = 'chromium' AND browser_channel IN ('chrome', 'msedge'))
        )
    ),
    CONSTRAINT ui_browser_manifests_viewport_valid CHECK (
        viewport_width BETWEEN 320 AND 3840
        AND viewport_height BETWEEN 240 AND 2160
    ),
    CONSTRAINT ui_browser_manifests_review_valid CHECK (
        review_status IN ('draft', 'approved', 'rejected')
        AND (
            (review_status = 'draft' AND reviewed_by IS NULL)
            OR (review_status <> 'draft' AND reviewed_by IS NOT NULL AND btrim(reviewed_by) <> '')
        )
    ),
    CONSTRAINT ui_browser_manifests_digest_sha256 CHECK (
        payload_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ui_browser_manifests_plan_fk FOREIGN KEY (
        ui_execution_plan_id, project_id
    ) REFERENCES ui_execution_plans(ui_execution_plan_id, project_id),
    CONSTRAINT ui_browser_manifests_scope_identity_unique UNIQUE (
        browser_manifest_id, project_id
    )
);

CREATE UNIQUE INDEX ui_browser_manifests_approved_plan_unique
    ON ui_browser_manifests (ui_execution_plan_id)
    WHERE review_status = 'approved';

CREATE TABLE ui_browser_scenario_specs (
    browser_manifest_id text NOT NULL,
    project_id text NOT NULL,
    ui_execution_plan_id text NOT NULL,
    scenario_id text NOT NULL,
    scenario_version_id text NOT NULL,
    trigger_path text NOT NULL,
    impact_item_refs jsonb NOT NULL,
    actions jsonb NOT NULL,
    assertions jsonb NOT NULL,
    redaction_locators jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ui_browser_scenario_specs_fields_not_blank CHECK (
        btrim(scenario_id) <> ''
        AND btrim(scenario_version_id) <> ''
        AND btrim(trigger_path) <> ''
    ),
    CONSTRAINT ui_browser_scenario_specs_arrays_valid CHECK (
        jsonb_typeof(impact_item_refs) = 'array'
        AND jsonb_array_length(impact_item_refs) > 0
        AND jsonb_typeof(actions) = 'array'
        AND jsonb_typeof(assertions) = 'array'
        AND jsonb_array_length(assertions) > 0
        AND jsonb_typeof(redaction_locators) = 'array'
    ),
    CONSTRAINT ui_browser_scenario_specs_manifest_fk FOREIGN KEY (
        browser_manifest_id, project_id
    ) REFERENCES ui_browser_manifests(browser_manifest_id, project_id),
    CONSTRAINT ui_browser_scenario_specs_exact_plan_scenario_fk FOREIGN KEY (
        ui_execution_plan_id, project_id, scenario_id, scenario_version_id
    ) REFERENCES ui_execution_plan_scenarios(
        ui_execution_plan_id, project_id, scenario_id, scenario_version_id
    ),
    CONSTRAINT ui_browser_scenario_specs_identity PRIMARY KEY (
        browser_manifest_id, scenario_id
    )
);

CREATE INDEX ui_browser_scenario_specs_plan_idx
    ON ui_browser_scenario_specs (project_id, ui_execution_plan_id, scenario_id);
