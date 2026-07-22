ALTER TABLE document_relation_builds
    ADD COLUMN plan_digest text;

ALTER TABLE document_relation_builds
    ADD CONSTRAINT document_relation_builds_plan_digest_sha256 CHECK (
        plan_digest IS NULL OR plan_digest ~ '^[0-9a-f]{64}$'
    );

COMMENT ON COLUMN document_relation_builds.plan_digest IS
    'SHA-256 of the versioned normalized relation and unresolved ledgers; NULL marks a legacy build that must be rebuilt before use';
