-- 0001_init.sql — esquema base del motor (F0.1)
-- Postgres 15+ con pgvector.
-- Las tablas no nombran a ninguna instancia: la instancia es una fila en `instances`.

create extension if not exists vector;
create extension if not exists pgcrypto;

create schema if not exists motor;
set search_path to motor, public;

-- ---------------------------------------------------------------- instancias y sujetos

create table if not exists instances (
    id          text primary key,
    created_at  timestamptz not null default now()
);

create table if not exists subjects (
    id            uuid primary key default gen_random_uuid(),
    instance_id   text not null references instances(id),
    external_ref  text not null,           -- referencia del canal (p. ej. chat id)
    created_at    timestamptz not null default now(),
    unique (instance_id, external_ref)
);

-- ---------------------------------------------------------------- eventos append-only

create table if not exists events (
    id                 uuid primary key default gen_random_uuid(),
    instance_id        text not null references instances(id),
    subject_id         uuid references subjects(id),
    kind               text not null,
    payload            jsonb not null default '{}'::jsonb,
    idempotency_key    text not null,
    occurred_at        timestamptz not null default now(),
    recorded_at        timestamptz not null default now(),
    corrects_event_id  uuid references events(id),
    unique (instance_id, idempotency_key)
);
create index if not exists events_subject_time_idx on events (instance_id, subject_id, occurred_at);
create index if not exists events_kind_idx on events (instance_id, kind, occurred_at);

create or replace function forbid_mutation() returns trigger
language plpgsql as $$
begin
    raise exception 'append-only: % no permitido en %', tg_op, tg_table_name;
end;
$$;

drop trigger if exists events_append_only on events;
create trigger events_append_only
    before update or delete on events
    for each row execute function forbid_mutation();

-- ---------------------------------------------------------------- episodios (texto + embedding)

create table if not exists episodes (
    id          uuid primary key default gen_random_uuid(),
    instance_id text not null references instances(id),
    subject_id  uuid not null references subjects(id),
    event_id    uuid not null references events(id) unique,
    text        text not null,
    created_at  timestamptz not null default now()
);

-- Un embedding por (episodio, modelo). Dimension libre en la columna; el
-- indice se crea por modelo con la dimension fija de ese modelo. Cambiar
-- de modelo = nueva fila por episodio + nuevo indice; el anterior sigue
-- vivo hasta que se decida borrarlo.
create table if not exists episode_embeddings (
    episode_id  uuid not null references episodes(id),
    model       text not null,
    dim         int  not null,
    embedding   vector not null,
    created_at  timestamptz not null default now(),
    primary key (episode_id, model)
);
-- Indice del modelo por defecto (ver instances/<x>/config.yaml). Si se cambia el modelo,
-- agregar otro indice con su dimension y su predicado:
create index if not exists episode_embeddings_text3small_idx
    on episode_embeddings using hnsw ((embedding::vector(1536)) vector_cosine_ops)
    where model = 'openai/text-embedding-3-small';

-- ---------------------------------------------------------------- jobs (scheduler idempotente)

create table if not exists jobs (
    id               uuid primary key default gen_random_uuid(),
    instance_id      text not null references instances(id),
    subject_id       uuid references subjects(id),
    kind             text not null,
    run_at           timestamptz not null,
    idempotency_key  text not null unique,
    payload          jsonb not null default '{}'::jsonb,
    status           text not null default 'pending' check (status in ('pending','running','done','failed','cancelled')),
    attempts         int  not null default 0,
    lease_until      timestamptz,
    finished_at      timestamptz,
    result           jsonb,
    error            text,
    created_at       timestamptz not null default now()
);
create index if not exists jobs_due_idx on jobs (run_at) where status in ('pending','running');

-- ---------------------------------------------------------------- registro de accesos (lecturas desde la app)

create table if not exists access_log (
    id           uuid primary key default gen_random_uuid(),
    instance_id  text not null references instances(id),
    what         text not null,     -- que se leyo (tabla / objeto / version)
    why          text not null,     -- lo que declare quien lee
    actor        text not null,     -- 'app' | 'operador'
    created_at   timestamptz not null default now()
);
drop trigger if exists access_log_append_only on access_log;
create trigger access_log_append_only
    before update or delete on access_log
    for each row execute function forbid_mutation();

-- ---------------------------------------------------------------- control de migraciones

create table if not exists schema_migrations (
    name        text primary key,
    applied_at  timestamptz not null default now()
);
insert into schema_migrations (name) values ('0001_init') on conflict do nothing;
