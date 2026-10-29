-- Crypto State Radar research schema (PostgreSQL)

create table if not exists model_versions (
  version text primary key,
  created_at timestamptz not null default now(),
  config jsonb not null,
  notes text
);

create table if not exists market_observations (
  symbol text not null,
  observed_at timestamptz not null,
  open numeric, high numeric, low numeric, close numeric, volume numeric,
  open_interest numeric,
  funding_rate numeric,
  source text not null default 'binance',
  primary key(symbol, observed_at)
);

create table if not exists signal_journal (
  signal_id uuid primary key,
  detected_at timestamptz not null,
  symbol text not null,
  model_version text not null references model_versions(version),
  detection_price numeric not null,
  pump_score numeric,
  dump_score numeric,
  state text not null,
  ttl_minutes integer,
  features jsonb not null,
  reasons jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists signal_journal_symbol_time on signal_journal(symbol, detected_at desc);

create table if not exists state_transitions (
  transition_id bigserial primary key,
  signal_id uuid references signal_journal(signal_id),
  symbol text not null,
  occurred_at timestamptz not null,
  model_version text not null,
  previous_state text,
  new_state text not null,
  price numeric,
  open_interest numeric,
  funding_rate numeric,
  reason jsonb not null
);

create index if not exists state_transitions_symbol_time on state_transitions(symbol, occurred_at desc);

create table if not exists validations (
  signal_id uuid not null references signal_journal(signal_id),
  horizon text not null check (horizon in ('1h','3h','6h','12h','24h','3d','7d')),
  validated_at timestamptz not null,
  end_price numeric,
  return_pct numeric,
  mfe_pct numeric,
  mae_pct numeric,
  event_label text,
  metrics jsonb not null default '{}'::jsonb,
  primary key(signal_id, horizon)
);

create table if not exists experiment_runs (
  experiment_id uuid primary key,
  model_version text not null,
  started_at timestamptz not null,
  ended_at timestamptz,
  parameters jsonb not null,
  metrics jsonb,
  notes text
);

-- Intentionally no UPDATE trigger here: application code must treat signal_journal
-- as immutable. Outcome data belongs in validations; state history belongs in
-- state_transitions. Production DB permissions should later deny UPDATE/DELETE
-- on signal_journal to the runtime role.
