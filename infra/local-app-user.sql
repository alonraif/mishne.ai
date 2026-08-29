-- Local development only. Creates the login user the API connects as.
--
--   psql "postgresql://mishne:mishne@localhost:5432/mishne" -f infra/local-app-user.sql
--
-- Run this once, AFTER `alembic upgrade head` — migration 0001 creates the
-- `mishne_app` group role this user is granted.
--
-- Why the API does not just connect as `mishne`: `mishne` is the superuser
-- docker-compose creates, and a superuser bypasses row-level security entirely.
-- Every policy in the schema would be present, correct, and doing nothing.
--
-- Staging and production create their login user through secrets management,
-- with a real password. The credential below is deliberately worthless and
-- exists only so that a fresh clone works with the default `app_database_url`
-- in src/mishne/config.py.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mishne_local') THEN
        CREATE ROLE mishne_local LOGIN PASSWORD 'mishne_local';
    END IF;
END
$$;

GRANT mishne_app TO mishne_local;

-- Neither of these may ever be granted to an application role: both bypass RLS
-- with no error and no log line.
ALTER ROLE mishne_local NOSUPERUSER NOBYPASSRLS;
