-- Civic Record Ledger, initial schema.
-- Every fetched document is hashed, stored once, and never rewritten.
-- document_versions is append-only: a new capture is a new row, enforced below.

create extension if not exists pgcrypto;

create table bodies (
    id              uuid primary key default gen_random_uuid(),
    name            text not null,
    jurisdiction    text not null,
    source_type     text not null check (source_type in ('legistar', 'granicus', 'pdf_directory', 'primegov')),
    source_config   jsonb not null default '{}'::jsonb,
    active          boolean not null default true,
    created_at      timestamptz not null default now(),
    unique (name, jurisdiction)
);

create table meetings (
    id              uuid primary key default gen_random_uuid(),
    body_id         uuid not null references bodies(id),
    meeting_date    date not null,
    source_url      text not null,
    first_seen_at   timestamptz not null default now(),
    unique (body_id, meeting_date, source_url)
);
create index meetings_body_id_idx on meetings(body_id);

create table documents (
    id              uuid primary key default gen_random_uuid(),
    meeting_id      uuid not null references meetings(id),
    doc_type        text not null check (doc_type in ('agenda', 'minutes', 'attachment', 'other')),
    source_url      text not null,
    first_seen_at   timestamptz not null default now(),
    unique (meeting_id, source_url)
);
create index documents_meeting_id_idx on documents(meeting_id);

-- Append-only: a new capture is always a new row, linked to its predecessor.
-- (document_id, sha256) unique so an unchanged source produces zero new rows
-- on a re-run rather than relying on caller discipline alone.
create table document_versions (
    id              uuid primary key default gen_random_uuid(),
    document_id     uuid not null references documents(id),
    sha256          text not null,
    storage_path    text not null,
    captured_at     timestamptz not null default now(),
    text_extracted  text,
    prev_version_id uuid references document_versions(id),
    diff_summary    text,
    unique (document_id, sha256)
);
create index document_versions_document_id_idx on document_versions(document_id);

create or replace function forbid_document_version_mutation()
returns trigger as $$
begin
    raise exception 'document_versions is append-only: % is not permitted', tg_op;
end;
$$ language plpgsql;

create trigger document_versions_no_update
    before update on document_versions
    for each row execute function forbid_document_version_mutation();

create trigger document_versions_no_delete
    before delete on document_versions
    for each row execute function forbid_document_version_mutation();

create table agenda_items (
    id                      uuid primary key default gen_random_uuid(),
    meeting_id              uuid not null references meetings(id),
    document_version_id     uuid not null references document_versions(id),
    item_number             text,
    title                   text not null,
    action_type             text,
    dollar_amount           numeric,
    addresses               jsonb not null default '[]'::jsonb,
    entities                jsonb not null default '[]'::jsonb,
    extraction_model        text,
    extraction_prompt_version text,
    created_at              timestamptz not null default now()
);
create index agenda_items_meeting_id_idx on agenda_items(meeting_id);
create index agenda_items_document_version_id_idx on agenda_items(document_version_id);

create table mentions (
    id              uuid primary key default gen_random_uuid(),
    agenda_item_id  uuid not null references agenda_items(id),
    source          text not null,
    url             text not null,
    published_at    timestamptz,
    matched_entity  text,
    created_at      timestamptz not null default now(),
    unique (agenda_item_id, url)
);
create index mentions_agenda_item_id_idx on mentions(agenda_item_id);

create table salience_scores (
    id              uuid primary key default gen_random_uuid(),
    agenda_item_id  uuid not null references agenda_items(id),
    record_activity numeric not null,
    coverage_count  integer not null,
    ratio           numeric not null,
    computed_at     timestamptz not null default now()
);
create index salience_scores_agenda_item_id_idx on salience_scores(agenda_item_id);

create table alerts (
    id              uuid primary key default gen_random_uuid(),
    agenda_item_id  uuid not null references agenda_items(id),
    channel         text not null check (channel in ('slack', 'email')),
    sent_at         timestamptz,
    opened_at       timestamptz,
    receipt_url     text not null
);
create index alerts_agenda_item_id_idx on alerts(agenda_item_id);

create table review_status (
    id              uuid primary key default gen_random_uuid(),
    agenda_item_id  uuid not null unique references agenda_items(id),
    status          text not null default 'New' check (status in ('New', 'Reviewed', 'Pitched', 'Published')),
    owner           text,
    updated_at      timestamptz not null default now()
);

-- Internal service only: no anon/public access to any table.
-- The scanner and receipt-page backend use the service role, which bypasses RLS.
alter table bodies enable row level security;
alter table meetings enable row level security;
alter table documents enable row level security;
alter table document_versions enable row level security;
alter table agenda_items enable row level security;
alter table mentions enable row level security;
alter table salience_scores enable row level security;
alter table alerts enable row level security;
alter table review_status enable row level security;

-- Private bucket for original captured files (PDFs, HTML), keyed by content hash.
insert into storage.buckets (id, name, public)
values ('document-snapshots', 'document-snapshots', false)
on conflict (id) do nothing;
