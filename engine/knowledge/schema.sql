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
