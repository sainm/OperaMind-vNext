ALTER TABLE test_data_identity_bindings
    ADD COLUMN identity_provider_type text,
    ADD COLUMN identity_provider_ref text,
    ADD COLUMN screen_identity_values jsonb,
    ADD COLUMN record_scope_locator jsonb,
    ADD CONSTRAINT test_data_identity_bindings_provider_consistent CHECK (
        (
            identity_provider_type IS NULL
            AND identity_provider_ref IS NULL
            AND screen_identity_values IS NULL
            AND record_scope_locator IS NULL
        )
        OR
        (
            identity_provider_type IN ('database', 'api', 'ui', 'hybrid')
            AND btrim(identity_provider_ref) <> ''
            AND identity_provider_ref ~ '^[A-Za-z][A-Za-z0-9._-]{0,127}$'
            AND jsonb_typeof(screen_identity_values) = 'array'
            AND jsonb_array_length(screen_identity_values) > 0
            AND jsonb_typeof(record_scope_locator) = 'object'
            AND screen_identity_values -> 0 = screen_key
            AND record_scope_locator = screen_locator
        )
    );

COMMENT ON COLUMN test_data_identity_bindings.identity_provider_ref IS
    'Non-secret configured DataIdentityProvider reference used for this frozen binding';
