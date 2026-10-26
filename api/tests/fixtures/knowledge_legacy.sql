-- Knowledge store. Everything lives in schema "knowledge" so it never collides with the
-- estate schema "gmf" or the API's tables. Idempotent: safe to run on every start.
-- Vectors are 384 dimensions (BAAI/bge-small-en-v1.5, or the deterministic hash fallback).

CREATE SCHEMA IF NOT EXISTS knowledge;

CREATE TABLE IF NOT EXISTS knowledge.meta (
    key   text PRIMARY KEY,
    value text NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge.graph_nodes (
    project_id text  NOT NULL,
    id         text  NOT NULL,
    kind       text  NOT NULL,
    name       text  NOT NULL,
    attrs      jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (project_id, id)
);

CREATE TABLE IF NOT EXISTS knowledge.graph_edges (
    project_id text  NOT NULL,
    src        text  NOT NULL,
    dst        text  NOT NULL,
    kind       text  NOT NULL,
    attrs      jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (project_id, src, dst, kind)
);
CREATE INDEX IF NOT EXISTS graph_edges_dst_idx ON knowledge.graph_edges (project_id, dst);

CREATE TABLE IF NOT EXISTS knowledge.chunks (
    id         text PRIMARY KEY,
    project_id text NOT NULL,
    process_id text NOT NULL,
    kind       text NOT NULL CHECK (kind IN ('summary', 'rule', 'finding', 'decision', 'design')),
    text       text NOT NULL,
    embedding  vector(384) NOT NULL,
    tsv        tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);
CREATE INDEX IF NOT EXISTS chunks_project_idx ON knowledge.chunks (project_id, process_id);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON knowledge.chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON knowledge.chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

CREATE TABLE IF NOT EXISTS knowledge.memory_items (
    id         text PRIMARY KEY,
    project_id text NOT NULL,
    kind       text NOT NULL CHECK (kind IN ('decision', 'pattern', 'mapping', 'note', 'seed')),
    text       text NOT NULL,
    context    jsonb NOT NULL DEFAULT '{}'::jsonb,
    source     text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    embedding  vector(384) NOT NULL,
    tsv        tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);
CREATE INDEX IF NOT EXISTS memory_items_project_idx ON knowledge.memory_items (project_id, kind);
CREATE INDEX IF NOT EXISTS memory_items_tsv_idx ON knowledge.memory_items USING gin (tsv);
CREATE INDEX IF NOT EXISTS memory_items_embedding_idx
    ON knowledge.memory_items USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- ---------------------------------------------------------------- test rows (api/tests/test_memory_migrate.py)
-- The DDL above is the earlier release's engine/knowledge/schema.sql, verbatim. The test fills
-- @PROJECT@, @PROCESS@ (the API process id), @PROCESS_NAME@ and @BACKEND@ (the embedding
-- backend the rows' vectors came from); it adds one seed row per shipped pattern itself. The
-- chunk texts are what the earlier release's index_process stored for the analysis rows the
-- test seeds (analysis.chunk_entries).

INSERT INTO knowledge.meta (key, value) VALUES ('embedding_backend', '@BACKEND@');

INSERT INTO knowledge.chunks (id, project_id, process_id, kind, text, embedding) VALUES
  ('chunk:@PROJECT@:@PROCESS@:summary:legacy0001', '@PROJECT@', '@PROCESS@', 'summary',
   'Summary of @PROCESS_NAME@: Legacy summary of dealer funding.',
   array_fill(0.05::real, ARRAY[384])::vector),
  ('chunk:@PROJECT@:@PROCESS@:rule:legacy0002', '@PROJECT@', '@PROCESS@', 'rule',
   'Rule R1 of @PROCESS_NAME@ (approved): Funding requests above the dealer cap are held.',
   array_fill(0.04::real, ARRAY[384])::vector);

INSERT INTO knowledge.memory_items (id, project_id, kind, text, context, source, created_by,
                                    created_at, embedding) VALUES
  ('mem_legacy0dec1', '@PROJECT@', 'decision',
   'Rule R1 of dealer funding: requests above the cap are held for review.',
   '{"process_id": "@PROCESS@", "rule_key": "R1", "user": "sme.vicky"}', 'sme', 'sme.vicky',
   '2026-03-02T10:00:00+00', array_fill(0.03::real, ARRAY[384])::vector),
  ('mem_legacy0map1', '@PROJECT@', 'mapping',
   'Map the dealer lookup activity to DealerRepository.FindAsync.',
   '{}', 'sme', 'arch.sam', '2026-03-03T11:30:00+00', array_fill(0.02::real, ARRAY[384])::vector);
