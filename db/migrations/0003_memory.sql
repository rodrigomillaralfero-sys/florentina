-- 0003_memory.sql — memoria recuperable y conocimiento base (F0.3)
-- Episodios: la tabla nacio en 0001; aqui se completa (tipo, ocurrencia, metadata) y se vuelve append-only.
-- Conocimiento base (costura 7): fuentes con version vigente por hash, trozos y sus embeddings.

set search_path to motor, public;

-- ---------------------------------------------------------------- episodios

alter table episodes add column if not exists kind        text not null default 'exchange';
alter table episodes add column if not exists occurred_at timestamptz not null default now();
alter table episodes add column if not exists metadata    jsonb not null default '{}'::jsonb;
create index if not exists episodes_subject_time_idx on episodes (instance_id, subject_id, occurred_at);

drop trigger if exists episodes_append_only on episodes;
create trigger episodes_append_only
    before update or delete on episodes
    for each row execute function forbid_mutation();

alter table episode_embeddings add column if not exists usage jsonb not null default '{}'::jsonb;
drop trigger if exists episode_embeddings_append_only on episode_embeddings;
create trigger episode_embeddings_append_only
    before update or delete on episode_embeddings
    for each row execute function forbid_mutation();

-- ---------------------------------------------------------------- conocimiento base

-- Version vigente de cada fuente (archivo de knowledge/). Es configuracion del operador, no
-- historia de las personas: se actualiza en sitio. current_hash null = la fuente fue retirada.
create table if not exists knowledge_sources (
    instance_id   text not null references instances(id),
    source        text not null,
    current_hash  text,
    chunks        int  not null default 0,
    loaded_at     timestamptz not null default now(),
    primary key (instance_id, source)
);

-- Trozos por version de fuente. Append-only: una version nueva agrega filas; las viejas quedan
-- pero dejan de recuperarse porque ya no son la version vigente.
create table if not exists knowledge_chunks (
    id           uuid primary key default gen_random_uuid(),
    instance_id  text not null references instances(id),
    source       text not null,
    source_hash  text not null,
    idx          int  not null,
    text         text not null,
    created_at   timestamptz not null default now(),
    unique (instance_id, source, source_hash, idx)
);
drop trigger if exists knowledge_chunks_append_only on knowledge_chunks;
create trigger knowledge_chunks_append_only
    before update or delete on knowledge_chunks
    for each row execute function forbid_mutation();

create table if not exists knowledge_embeddings (
    chunk_id    uuid not null references knowledge_chunks(id),
    model       text not null,
    dim         int  not null,
    embedding   vector not null,
    usage       jsonb not null default '{}'::jsonb,
    created_at  timestamptz not null default now(),
    primary key (chunk_id, model)
);
create index if not exists knowledge_embeddings_text3small_idx
    on knowledge_embeddings using hnsw ((embedding::vector(1536)) vector_cosine_ops)
    where model = 'openai/text-embedding-3-small';

insert into schema_migrations (name) values ('0003_memory') on conflict do nothing;
