ALTER TABLE verification_scenarios
    ADD COLUMN test_case_refs jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE verification_scenarios
    ADD CONSTRAINT verification_scenarios_test_case_refs_array
    CHECK (jsonb_typeof(test_case_refs) = 'array');
