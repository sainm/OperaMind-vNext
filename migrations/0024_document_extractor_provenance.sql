ALTER TABLE document_versions
    ADD COLUMN extractor_ref text;

UPDATE document_versions
SET extractor_ref = 'legacy-unversioned@0'
WHERE extractor_ref IS NULL;

ALTER TABLE document_versions
    ALTER COLUMN extractor_ref SET NOT NULL,
    ADD CONSTRAINT document_versions_extractor_ref_not_blank CHECK (
        btrim(extractor_ref) <> ''
    );
