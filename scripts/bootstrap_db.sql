-- Relay S1 · MT-3 role separation.
--
-- Three roles, and the separation is the whole point (design §2.4):
--   relay_owner   owns the schema; migrations run as this role.
--   relay_app     the runtime role. NOT the table owner, so FORCE ROW LEVEL
--                 SECURITY actually binds it. Explicitly NOBYPASSRLS.
--   relay_system  the only cross-tenant path (SystemRepository). BYPASSRLS,
--                 every call audited. Never used to serve a web request.
--
-- Run as a superuser: BYPASSRLS cannot be granted by a non-superuser.
--   sudo -u postgres psql -p 5433 -v db=relay_dev -f scripts/bootstrap_db.sql

\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_owner') THEN
    CREATE ROLE relay_owner LOGIN PASSWORD 'relay_owner' NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
    CREATE ROLE relay_app LOGIN PASSWORD 'relay_app' NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_system') THEN
    CREATE ROLE relay_system LOGIN PASSWORD 'relay_system' BYPASSRLS;
  END IF;
END
$$;

-- relay_app must never inherit relay_owner's ownership; keep them unrelated.
ALTER ROLE relay_app NOBYPASSRLS;
ALTER ROLE relay_owner NOBYPASSRLS;
ALTER ROLE relay_system BYPASSRLS;
