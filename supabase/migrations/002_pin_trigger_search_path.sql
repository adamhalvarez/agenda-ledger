-- Closes a linter WARN (function_search_path_mutable): pin the trigger
-- function's search_path instead of leaving it caller-dependent.
create or replace function forbid_document_version_mutation()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
    raise exception 'document_versions is append-only: % is not permitted', tg_op;
end;
$$;
