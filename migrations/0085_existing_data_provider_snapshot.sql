ALTER TABLE existing_test_data_registrations
    ADD COLUMN provider_revision integer,
    ADD COLUMN provider_digest text,
    ADD CONSTRAINT existing_test_data_registrations_provider_revision_valid CHECK (
        provider_revision IS NULL OR provider_revision >= 1
    ),
    ADD CONSTRAINT existing_test_data_registrations_provider_digest_valid CHECK (
        provider_digest IS NULL OR provider_digest ~ '^[0-9a-f]{64}$'
    ),
    ADD CONSTRAINT existing_test_data_registrations_provider_snapshot_pair CHECK (
        (provider_revision IS NULL) = (provider_digest IS NULL)
    );

COMMENT ON COLUMN existing_test_data_registrations.provider_digest IS
    'Canonical digest of the reviewed Provider configuration used for candidate resolution';
