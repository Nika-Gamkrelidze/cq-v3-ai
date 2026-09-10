-- Server health: host samples and per-tenant request load behind the console's Health tab.
--
-- Two tables, two questions. `system_metrics` answers "how is the box doing" — one row per
-- sampler tick (services/health_metrics.HostSampler, run by the api's lifespan every
-- `health.sample_interval_s` seconds). `tenant_load` answers "who is using it" — one row per
-- (minute, principal kind, client) with the request counters the timing middleware adds up
-- and the worker's `health_purge` duty trims to `health.retention_days`.
--
-- Why request wall time and not a CPU figure per tenant: analyze / transcription / the AI
-- calls all run INSIDE the request, so the milliseconds a tenant kept a request open is the
-- honest share of the server's time it consumed. There is no per-tenant CPU counter to read.
--
-- Not tenant-scoped in the usual sense: these tables are read by the superadmin only
-- (routers/admin.py); `client_id` here is a label, never a filter a tenant could reach.
--
-- Idempotent, like every migration here: CREATE IF NOT EXISTS only.

CREATE TABLE IF NOT EXISTS system_metrics (
    ts               timestamptz PRIMARY KEY,
    cpu_pct          real,
    cpu_count        integer,
    load1            real,
    load5            real,
    load15           real,
    mem_total_mb     integer,
    mem_used_mb      integer,
    mem_available_mb integer,
    swap_used_mb     integer,
    disk             jsonb,        -- [{mount, total_gb, used_gb, pct}] — '/' plus MEDIA_ROOT when it is another device
    disk_read_mb_s   real,
    disk_write_mb_s  real,
    net_rx_mb_s      real,
    net_tx_mb_s      real,
    api_rss_mb       real,
    api_cpu_pct      real,
    api_open_fds     integer,
    db_size_mb       real,
    db_pool_size     integer,
    db_pool_used     integer,
    active_jobs      integer,
    uptime_s         bigint
);

COMMENT ON TABLE system_metrics IS
  'One row per sampler tick. Host cpu/load/memory are the HOST''s (psutil reads /proc inside '
  'the container); network counters are the container''s own interfaces. NULL means that one '
  'reading failed — the sample is kept anyway.';

CREATE TABLE IF NOT EXISTS tenant_load (
    bucket         timestamptz NOT NULL,             -- the minute, floored
    principal_kind text        NOT NULL,             -- tenant | superadmin | user | anonymous | unknown
    client_key     text        NOT NULL DEFAULT '',  -- client_id as text, '' when there is none
    client_id      uuid,                              -- same value typed, for the join to clients
    requests       integer     NOT NULL DEFAULT 0,
    errors         integer     NOT NULL DEFAULT 0,   -- status >= 500 only: a 4xx is the caller's
    total_ms       bigint      NOT NULL DEFAULT 0,
    max_ms         integer     NOT NULL DEFAULT 0,
    bytes_in       bigint      NOT NULL DEFAULT 0,
    bytes_out      bigint      NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket, principal_kind, client_key)
);
-- The purge and every range query walk this by time; the PK leads on bucket too, but the
-- range scan should not have to carry the two text columns.
CREATE INDEX IF NOT EXISTS idx_tenant_load_bucket ON tenant_load(bucket);

COMMENT ON COLUMN tenant_load.client_key IS
  'Text copy of client_id (or '''' for anonymous / operator traffic) so it can sit in the '
  'primary key — a NULL uuid would never collide with itself and the upsert would insert a '
  'new row per request.';
COMMENT ON COLUMN tenant_load.client_id IS
  'Deliberately no FK: a deleted tenant keeps its share of the past hour''s load.';
