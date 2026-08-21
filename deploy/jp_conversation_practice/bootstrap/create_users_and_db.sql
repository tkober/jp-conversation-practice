-- One-off bootstrap for the jp_conversation database on postgres-core.
--
-- Expects two psql variables, which bootstrap.sh supplies from the stack's
-- .env: `app_password` and `owner_password`. psql substitutes :'name' as a
-- correctly quoted string literal, so no shell-side text substitution is
-- involved and passwords never reach a command line.
--
-- Idempotent: roles are created only when missing, and ALTER ROLE then sets
-- the password either way, so re-running it also rotates passwords.

-- Create the roles if they do not exist yet. \gexec runs the SELECT's result
-- as a statement, which is how a conditional CREATE is expressed in plain SQL.
SELECT 'CREATE ROLE jp_conversation_owner WITH LOGIN'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'jp_conversation_owner')
\gexec

SELECT 'CREATE ROLE jp_conversation_app WITH LOGIN'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'jp_conversation_app')
\gexec

ALTER ROLE jp_conversation_owner WITH LOGIN PASSWORD :'owner_password';
ALTER ROLE jp_conversation_app WITH LOGIN PASSWORD :'app_password';

-- CREATE DATABASE cannot run in a transaction or a DO block, so it takes the
-- same conditional form.
SELECT 'CREATE DATABASE jp_conversation OWNER jp_conversation_owner'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'jp_conversation')
\gexec

GRANT CONNECT ON DATABASE jp_conversation TO jp_conversation_app;
