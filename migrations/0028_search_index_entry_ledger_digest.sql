ALTER TABLE document_search_vectors
    ADD COLUMN embedding_digest text GENERATED ALWAYS AS (
        encode(sha256(public.vector_send(embedding)), 'hex')
    ) STORED NOT NULL;

ALTER TABLE document_search_vectors
    ADD CONSTRAINT document_search_vectors_embedding_digest_sha256 CHECK (
        embedding_digest ~ '^[0-9a-f]{64}$'
    );

COMMENT ON COLUMN document_search_vectors.embedding_digest IS
    'Generated SHA-256 of the exact pgvector binary value used by retrieval';

ALTER TABLE search_index_builds
    ADD COLUMN entry_ledger_digest text;

ALTER TABLE search_index_builds
    ADD CONSTRAINT search_index_builds_entry_ledger_digest_sha256 CHECK (
        entry_ledger_digest IS NULL OR entry_ledger_digest ~ '^[0-9a-f]{64}$'
    );

COMMENT ON COLUMN search_index_builds.entry_ledger_digest IS
    'SHA-256 of the versioned normalized entry, keyword, vector identity, and vector-content ledger; NULL on ready/stale marks a legacy Build that must be rebuilt before use';
