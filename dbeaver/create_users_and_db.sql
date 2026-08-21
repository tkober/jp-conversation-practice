-- One-off bootstrap for the jp_conversation database on a shared Postgres
-- server. Run as the postgres superuser, substituting the ${...} placeholders
-- with the values from the deployment's .env:
--
--   docker exec -i postgres-core psql -U postgres < create_users_and_db.sql
--
-- Then run grant_privileges.sql against the new database.
--
-- Note: paste the passwords WITHOUT the surrounding quotes, even if the .env
-- writes them as DB_PASSWORD="…". Docker Compose strips those quotes before
-- the container sees the value, so a role created with them can never be
-- logged into.

-- Create Roles
CREATE ROLE jp_conversation_owner
  WITH LOGIN
  PASSWORD '${DB_OWNER_PASSWORD}';

CREATE ROLE jp_conversation_app
  WITH LOGIN
  PASSWORD '${DB_PASSWORD}';

-- Create Database
CREATE DATABASE jp_conversation
  OWNER jp_conversation_owner;

-- Allow app user to connect
GRANT CONNECT ON DATABASE jp_conversation
  TO jp_conversation_app;
