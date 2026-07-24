ALTER TABLE snapshot_memberships
    ADD COLUMN selected_variant_ids jsonb;

UPDATE snapshot_memberships
SET selected_variant_ids = jsonb_build_array(selected_variant_id);

ALTER TABLE snapshot_memberships
    ALTER COLUMN selected_variant_ids SET NOT NULL,
    ADD CONSTRAINT snapshot_memberships_variants_valid CHECK (
        jsonb_typeof(selected_variant_ids) = 'array'
        AND jsonb_array_length(selected_variant_ids) > 0
        AND selected_variant_ids ->> 0 = selected_variant_id
    );

CREATE TABLE document_fact_variants (
    project_id text NOT NULL,
    document_snapshot_id text NOT NULL,
    document_version_id text NOT NULL,
    document_fact_id text PRIMARY KEY REFERENCES document_facts(document_fact_id),
    selected_variant_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_fact_variants_variant_not_blank CHECK (
        btrim(selected_variant_id) <> ''
    ),
    CONSTRAINT document_fact_variants_membership_fk FOREIGN KEY (
        project_id,
        document_snapshot_id,
        document_version_id
    ) REFERENCES snapshot_memberships (
        project_id,
        document_snapshot_id,
        document_version_id
    )
);

INSERT INTO document_fact_variants (
    project_id,
    document_snapshot_id,
    document_version_id,
    document_fact_id,
    selected_variant_id
)
SELECT fact.project_id,
       fact.document_snapshot_id,
       fact.document_version_id,
       fact.document_fact_id,
       membership.selected_variant_id
FROM document_facts AS fact
JOIN snapshot_memberships AS membership
  ON membership.project_id = fact.project_id
 AND membership.document_snapshot_id = fact.document_snapshot_id
 AND membership.document_version_id = fact.document_version_id;

CREATE INDEX document_fact_variants_snapshot_idx
    ON document_fact_variants (
        project_id,
        document_snapshot_id,
        selected_variant_id,
        document_fact_id
    );
