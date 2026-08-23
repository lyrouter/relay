-- Per-database extensions. Superuser only, one database at a time.
--
--   sudo -u postgres psql -p 5433 -d relay_dev -f scripts/bootstrap_extensions.sql
--
-- Why this is not an Alembic migration: pgroonga is not a trusted extension, so
-- CREATE EXTENSION requires a superuser — and migrations run as `relay_owner`,
-- which is deliberately not one. Making the migration work would mean handing
-- the migration role superuser, which is a much worse trade than one documented
-- provisioning step.
--
-- Extension provisioning therefore sits next to role creation in the deployment
-- handbook (AC-9's credentialed init step), not in the migration chain.
--
-- pgroonga: LOG-8's Chinese full-text search (F-2 — confirmed installable, so
--   the zhparser fallback is moot). Created now rather than in week 5 so that
--   "which image has pgroonga?" is answered while nothing depends on the answer.
--   The indexes themselves are LOG-8's work.
--
-- pgvector: deliberately absent. MT-5 has nothing to isolate in S1 (no
--   knowledge_unit table). It belongs to the RAG migration that creates those
--   tables — same database, same policy (relay.ports.search).

\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS pgroonga;
