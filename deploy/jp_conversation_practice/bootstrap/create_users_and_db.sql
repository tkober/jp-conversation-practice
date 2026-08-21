-- One-off bootstrap for the jp_conversation database on postgres-core.
-- Run as the postgres superuser, substituting the ${...} password placeholders
-- with the values from env/jp-conversation-practice-backend/.env:
--
--   docker exec -i postgres-core psql -U postgres < create_users_and_db.sql   (after substituting)

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
