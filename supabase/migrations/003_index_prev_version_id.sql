-- Closes a linter INFO (unindexed_foreign_keys): document_versions.prev_version_id
-- will be walked for version-history lookups starting in Phase 2.
create index document_versions_prev_version_id_idx on document_versions(prev_version_id);
