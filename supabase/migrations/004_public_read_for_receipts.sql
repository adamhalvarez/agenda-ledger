-- Public read access for receipt pages -- REDRAFT, not applied.
--
-- Supersedes an earlier draft of this same filename that used `using (true)`
-- (blanket anon SELECT on all rows) and flipped the document-snapshots
-- storage bucket to fully public. Both were rejected on review:
--
--   1. `using (true)` makes every row in these four tables anon-enumerable,
--      not just the one document version a given receipt link points to.
--      The existence of review_status (agenda_items only ever start life as
--      'New') is itself evidence that not everything captured here has been
--      through any kind of review, so a blanket predicate would publish the
--      entire corpus the moment this migration runs, not just what's been
--      decided safe to publish.
--   2. A public storage bucket is bucket-wide, applies to every object added
--      later, and cannot be revoked per object. An RLS policy on
--      storage.objects, scoped the same way the table policies are, gets
--      the same outcome without that one-way door.
--
-- This redraft adds an explicit, human-set gate instead of deriving a
-- predicate from existing columns. document_versions.publicly_released
-- defaults to false, so applying this migration by itself changes nothing
-- about live exposure -- every row stays exactly as unreadable to anon as
-- it is today, until a person flips a specific version's flag.
--
-- Why not derive this from review_status instead of adding a column: that
-- table hangs off agenda_items (status defaults to 'New', becomes
-- 'Reviewed' / 'Pitched' / 'Published' as an editorial pipeline
-- moves a *story*), not off document_versions directly, and only rows that
-- made it through extraction get an agenda_items row at all -- 'minutes'
-- and 'attachment' documents never do. Joining through it would (a) make
-- "safe to show this source document's full text to the public" the same
-- thing as "we published an article about it", which are different
-- questions, and (b) permanently exclude every minutes/attachment document
-- from ever getting a receipt page, even when a published story cites one.
-- That's a real design question -- worth deciding deliberately with intent,
-- not inheriting by accident from a table built for a different purpose.
--
-- Before setting publicly_released on any row: document_versions.text_extracted
-- has not been reviewed for personal information as of this draft. A live
-- sample pulled for this review already shows individual staff names and
-- direct phone numbers (e.g. a legislative assistant's name and direct
-- line) inside agenda header boilerplate, and minutes documents -- not
-- sampled here -- routinely carry public commenters' names read into the
-- record. Whether that's acceptable (it's already published on the
-- source city site this scrapes) is a real question, not a rubber stamp,
-- and it's a per-version judgment call, which is the other reason this is
-- a column a human sets, not a bulk default.

alter table document_versions
    add column publicly_released boolean not null default false;

comment on column document_versions.publicly_released is
    'Sets whether this captured version''s text_extracted and original file are readable by the anon role via the public receipt page. Defaults false. Set true only after a human has read this version''s extracted text for personal information (private individuals'' names, addresses, phone numbers -- not elected officials or public staff acting in their official capacity) and decided it is safe to publish. Never set by any automated pipeline (extraction, diffing, or otherwise).';

-- document_versions: exactly the row a receipt page reads, and only once
-- it's been marked released.
create policy "public read of released document versions" on document_versions
    for select
    to anon
    using (publicly_released);

-- documents: only a document that has at least one released version, never
-- the whole table.
create policy "public read of documents with a released version" on documents
    for select
    to anon
    using (
        exists (
            select 1
            from document_versions dv
            where dv.document_id = documents.id
              and dv.publicly_released
        )
    );

-- meetings: only a meeting that has at least one document with a released
-- version.
create policy "public read of meetings with a released version" on meetings
    for select
    to anon
    using (
        exists (
            select 1
            from documents d
            join document_versions dv on dv.document_id = d.id
            where d.meeting_id = meetings.id
              and dv.publicly_released
        )
    );

-- bodies: only a body that has at least one meeting with a released
-- version.
create policy "public read of bodies with a released version" on bodies
    for select
    to anon
    using (
        exists (
            select 1
            from meetings m
            join documents d on d.meeting_id = m.id
            join document_versions dv on dv.document_id = d.id
            where m.body_id = bodies.id
              and dv.publicly_released
        )
    );

-- document-snapshots storage: an object-level policy scoped to exactly the
-- objects a released document_version points to, instead of flipping the
-- bucket's `public` flag. storage.objects already has RLS enabled by
-- Supabase's own default (this migration does not touch that); this adds
-- one policy to it, scoped to this bucket only. Unlike a public bucket,
-- this is revocable per object: unset publicly_released on a version and
-- its file stops being readable, no bucket-wide change required.
create policy "public read of released document snapshots" on storage.objects
    for select
    to anon
    using (
        bucket_id = 'document-snapshots'
        and exists (
            select 1
            from document_versions dv
            where dv.storage_path = storage.objects.name
              and dv.publicly_released
        )
    );

-- Deliberately not in this migration:
--   * Any UPDATE/INSERT/DELETE policy for anon on any of these tables --
--     writes stay service-role-only, unchanged.
--   * Backfilling publicly_released = true on any existing row.
--   * Flipping storage.buckets.public for document-snapshots.
--   * A policy exposing signal_store in any way. Verified live 2026-09-03
--     against nvwllgwujqdwailnkels with the anon key via PostgREST: anon
--     has no USAGE grant and no table grants on schema signal_store
--     (information_schema.role_table_grants, has_schema_privilege), and
--     independently, PostgREST itself rejects any request naming that
--     schema (Accept-Profile: signal_store -> PGRST106 "Invalid schema:
--     signal_store"; only public and graphql_public are exposed). This
--     migration only adds policies inside schema public and on
--     storage.objects, so it does not change either fact.
