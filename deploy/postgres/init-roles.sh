#!/usr/bin/env bash
set -euo pipefail

app_password=$(< /run/secrets/postgres_app_password)
migrator_password=$(< /run/secrets/postgres_migrator_password)

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$app_password" --set=migrator_password="$migrator_password" <<'SQL'
CREATE ROLE redline_owner NOLOGIN;
CREATE ROLE redline_migrator LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
  PASSWORD :'migrator_password';
CREATE ROLE redline_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  PASSWORD :'app_password';

GRANT redline_owner TO redline_migrator;
ALTER DATABASE redline OWNER TO redline_owner;
CREATE SCHEMA redline AUTHORIZATION redline_owner;

REVOKE ALL ON DATABASE redline FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE redline TO redline_app, redline_migrator;
GRANT USAGE ON SCHEMA redline TO redline_app;
GRANT USAGE ON SCHEMA public TO redline_app;

ALTER DEFAULT PRIVILEGES FOR ROLE redline_owner IN SCHEMA redline
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO redline_app;
ALTER DEFAULT PRIVILEGES FOR ROLE redline_owner IN SCHEMA redline
  GRANT USAGE, SELECT ON SEQUENCES TO redline_app;
-- Le service de santé lit uniquement la révision Alembic dans public.
ALTER DEFAULT PRIVILEGES FOR ROLE redline_owner IN SCHEMA public
  GRANT SELECT ON TABLES TO redline_app;
SQL
