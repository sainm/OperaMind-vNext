ALTER TABLE test_data_identity_bindings
    ADD COLUMN identity_observations jsonb,
    ADD COLUMN identity_digest text,
    ADD CONSTRAINT test_data_identity_bindings_dom_identity_consistent CHECK (
        (
            identity_observations IS NULL
            AND identity_digest IS NULL
        )
        OR
        (
            jsonb_typeof(identity_observations) = 'object'
            AND jsonb_typeof(identity_observations -> 'business_unique_keys') = 'array'
            AND jsonb_array_length(identity_observations -> 'business_unique_keys') > 0
            AND jsonb_typeof(identity_observations -> 'screen_key') = 'object'
            AND identity_digest ~ '^[0-9a-f]{64}$'
        )
    );

COMMENT ON COLUMN test_data_identity_bindings.identity_digest IS
    'SHA-256 of frozen business_unique_keys and screen identity values; compared with DOM observation';
