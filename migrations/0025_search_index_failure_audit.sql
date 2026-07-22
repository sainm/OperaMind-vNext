ALTER TABLE search_index_builds
    ADD COLUMN failure_event_id text,
    ADD COLUMN failure_kind text,
    ADD COLUMN failure_actor text,
    ADD COLUMN failure_stale_before timestamptz;

UPDATE search_index_builds
SET failure_event_id = 'legacy-search-index-failure:' || search_index_build_id,
    failure_kind = 'legacy_unversioned',
    failure_actor = 'migration-0025',
    failure_reason = CASE
        WHEN failure_reason IS NULL OR btrim(failure_reason) = ''
            THEN 'legacy-unversioned failure reason unavailable'
        ELSE failure_reason
    END
WHERE status = 'failed';

ALTER TABLE search_index_builds
    ADD CONSTRAINT search_index_builds_failure_audit_consistent CHECK (
        (
            status = 'failed'
            AND failure_event_id IS NOT NULL
            AND btrim(failure_event_id) <> ''
            AND failure_kind IS NOT NULL
            AND failure_kind IN (
                'embedding_generation',
                'publish_validation',
                'publish_execution',
                'stale_recovery',
                'legacy_unversioned'
            )
            AND failure_actor IS NOT NULL
            AND btrim(failure_actor) <> ''
            AND failure_reason IS NOT NULL
            AND btrim(failure_reason) <> ''
            AND completed_at IS NOT NULL
        )
        OR (
            status <> 'failed'
            AND failure_event_id IS NULL
            AND failure_kind IS NULL
            AND failure_actor IS NULL
            AND failure_reason IS NULL
            AND failure_stale_before IS NULL
        )
    ),
    ADD CONSTRAINT search_index_builds_recovery_boundary_consistent CHECK (
        (
            failure_kind IS NOT DISTINCT FROM 'stale_recovery'
            AND failure_stale_before IS NOT NULL
        )
        OR (
            failure_kind IS DISTINCT FROM 'stale_recovery'
            AND failure_stale_before IS NULL
        )
    );

CREATE UNIQUE INDEX search_index_builds_failure_event_unique
    ON search_index_builds (failure_event_id)
    WHERE failure_event_id IS NOT NULL;
