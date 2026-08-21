-- Run as the postgres superuser to check the bootstrap actually took effect.
-- Both roles and the database must be listed; an empty result means
-- create_users_and_db.sql did not run (or ran against a different server).

SELECT rolname AS role, rolcanlogin AS can_login
  FROM pg_roles
 WHERE rolname IN ('jp_conversation_owner', 'jp_conversation_app')
 ORDER BY rolname;

SELECT datname AS database, pg_get_userbyid(datdba) AS owner
  FROM pg_database
 WHERE datname = 'jp_conversation';
