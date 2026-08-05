ALTER TABLE project_target_data_profiles
    DROP CONSTRAINT project_target_data_profiles_dialect_valid,
    ADD CONSTRAINT project_target_data_profiles_dialect_safe CHECK (
        dialect ~ '^[a-z][a-z0-9_]{0,31}$'
    );

COMMENT ON COLUMN project_target_data_profiles.dialect IS
    'Tested-system database Adapter key. Application registry remains authoritative and unsupported dialects fail closed.';
