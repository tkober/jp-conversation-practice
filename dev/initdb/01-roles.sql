-- Local mirror of the deployment bootstrap: the app never runs as the
-- superuser, so the owner/app split is exercised in development too.
--
-- Runs only on an empty data directory (Postgres' initdb hook). The database
-- itself already exists at this point, created from POSTGRES_DB.
CREATE ROLE jp_conversation_owner WITH LOGIN PASSWORD 'jp_conversation';
CREATE ROLE jp_conversation_app WITH LOGIN PASSWORD 'jp_conversation';

GRANT CONNECT ON DATABASE jp_conversation TO jp_conversation_app;

ALTER SCHEMA public OWNER TO jp_conversation_owner;
GRANT USAGE ON SCHEMA public TO jp_conversation_app;

-- The backend creates the tables at startup as the owner; the app role's
-- access comes from these defaults rather than an explicit GRANT.
ALTER DEFAULT PRIVILEGES FOR ROLE jp_conversation_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO jp_conversation_app;
ALTER DEFAULT PRIVILEGES FOR ROLE jp_conversation_owner IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO jp_conversation_app;
