-- Run against the jp_conversation database (NOT postgres) after
-- create_users_and_db.sql:
--
--   docker exec -i postgres-core psql -U postgres -d jp_conversation < grant_privileges.sql
--
-- The schema itself (tables, indexes) is created by the backend on startup,
-- connecting as jp_conversation_owner; the app role never runs DDL and gets
-- its access from the default privileges below.

ALTER SCHEMA public OWNER TO jp_conversation_owner;

GRANT USAGE ON SCHEMA public
  TO jp_conversation_app;

ALTER DEFAULT PRIVILEGES
  FOR ROLE jp_conversation_owner
  IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE
  ON TABLES
  TO jp_conversation_app;

ALTER DEFAULT PRIVILEGES
  FOR ROLE jp_conversation_owner
  IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE
  ON SEQUENCES
  TO jp_conversation_app;
