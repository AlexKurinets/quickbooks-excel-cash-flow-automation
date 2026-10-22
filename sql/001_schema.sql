-- One schema for the whole system. `cashflow migrate --reset` drops and rebuilds it.
create schema if not exists cashflow;
set search_path to cashflow;

-- A client company of the firm, connected to its own QuickBooks Online company (realm).
create table if not exists clients (
    id                    text primary key,
    name                  text not null,
    industry              text not null,
    realm_id              text not null unique,
    fiscal_year_start     int  not null default 1 check (fiscal_year_start between 1 and 12),
    basis                 text not null default 'Accrual' check (basis in ('Accrual', 'Cash')),
    connection_status     text not null default 'connected' check (connection_status in ('connected', 'expired')),
    connection_expired_at timestamptz,
    created_at            timestamptz not null default now()
);

-- OAuth tokens per client company. Intuit rotates the refresh token on every refresh, so a refresh
-- happens under `select ... for update` on this row and the new pair is written in that transaction.
create table if not exists qbo_tokens (
    client_id          text primary key references clients (id),
    access_token       text not null,
    access_expires_at  timestamptz not null,
    refresh_token      text not null,
    refresh_expires_at timestamptz not null,
    refreshed_at       timestamptz not null default now()
);

-- Last seen copy of each client's chart of accounts, so a new account is noticed the run it appears.
create table if not exists qbo_accounts (
    client_id      text not null references clients (id),
    account_id     text not null,
    name           text not null,
    account_type   text not null,
    account_subtype text not null,
    classification text not null,
    active         boolean not null default true,
    first_seen_at  timestamptz not null,
    last_seen_at   timestamptz not null,
    primary key (client_id, account_id)
);

-- Approved mappings only. A row exists here once a person has approved where an account goes.
create table if not exists account_mappings (
    client_id   text not null references clients (id),
    account_id  text not null,
    line_key    text not null,
    source      text not null check (source in ('onboarding', 'suggestion', 'manual')),
    approved_by text not null,
    approved_at timestamptz not null,
    primary key (client_id, account_id)
);

create table if not exists mapping_suggestions (
    id              bigserial primary key,
    client_id       text not null references clients (id),
    account_id      text not null,
    account_name    text not null,
    account_type    text not null,
    account_subtype text not null,
    classification  text not null,
    suggested_line  text,
    confidence      numeric(3, 2),
    reason          text not null,
    provider        text not null,
    status          text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
    decided_line    text,
    decided_by      text,
    decided_at      timestamptz,
    note            text,
    created_at      timestamptz not null,
    run_id          bigint
);
create unique index if not exists one_pending_per_account
    on mapping_suggestions (client_id, account_id) where status = 'pending';

create table if not exists runs (
    id              bigserial primary key,
    client_id       text not null references clients (id),
    period          date not null,          -- last day of the closed month
    as_of           timestamptz not null,   -- QuickBooks data as of this moment
    status          text not null check (status in ('running', 'ready', 'held', 'failed')),
    reason          text,
    started_at      timestamptz not null,
    finished_at     timestamptz,
    duration_ms     int,
    template_name   text,
    template_sha256 text,
    formula_count   int,
    workbook        bytea,
    workbook_sha256 text,
    figures         jsonb,
    created_by      text not null,
    delivered_at    timestamptz,
    delivered_by    text
);
create index if not exists runs_by_client_period on runs (client_id, period, id desc);

create table if not exists run_checks (
    run_id   bigint not null references runs (id) on delete cascade,
    ordinal  int not null,
    key      text not null,
    title    text not null,
    status   text not null check (status in ('pass', 'fail', 'warn', 'skipped')),
    blocking boolean not null,
    summary  text not null,
    detail   jsonb,
    primary key (run_id, key)
);

create table if not exists audit_log (
    id        bigserial primary key,
    at        timestamptz not null,
    actor     text not null,
    action    text not null,
    client_id text,
    detail    jsonb
);
