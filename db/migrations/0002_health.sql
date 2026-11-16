-- 0002_health.sql — salud tecnica (F0.2b)
-- Latidos, corridas del doctor y OK diario. Tabla aparte de `events` porque un latido cada
-- cinco minutos son ~300 filas al dia y no son historia de las personas. Append-only como todo.
-- Las alertas y los cortacircuitos si van en `events` (son pocos y son historia de la instancia).

set search_path to motor, public;

create table if not exists health_log (
    id           uuid primary key default gen_random_uuid(),
    instance_id  text not null references instances(id),
    kind         text not null check (kind in ('heartbeat', 'doctor', 'daily')),
    ok           boolean not null,
    detail       jsonb not null default '{}'::jsonb,
    at           timestamptz not null default now()
);

create index if not exists health_log_instance_kind_at on health_log (instance_id, kind, at desc);

drop trigger if exists health_log_append_only on health_log;
create trigger health_log_append_only
    before update or delete on health_log
    for each row execute function forbid_mutation();
