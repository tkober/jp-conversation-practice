#!/usr/bin/env bash
# Create the roles and database for this stack on postgres-core.
#
# Reads the passwords from the stack's .env and passes them to psql through
# stdin, so they never appear on a command line, in `ps`, or in shell history.
# Idempotent: running it again after rotating the passwords updates them.
#
#   ./bootstrap.sh
#   PG_CONTAINER=other-postgres ./bootstrap.sh
#   ENV_FILE=/path/to/.env ./bootstrap.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="${ENV_FILE:-$here/../env/jp-conversation-practice-backend/.env}"
container="${PG_CONTAINER:-postgres-core}"

if [ ! -f "$env_file" ]; then
  echo "Missing $env_file — copy .env.example and fill in the passwords." >&2
  exit 1
fi

read_var() {
  # Last occurrence wins, matching how the file would be sourced.
  grep -E "^$1=" "$env_file" | tail -n1 | cut -d= -f2-
}

app_password="$(read_var DB_PASSWORD)"
owner_password="$(read_var DB_OWNER_PASSWORD)"

if [ -z "$app_password" ] || [ -z "$owner_password" ]; then
  echo "DB_PASSWORD and DB_OWNER_PASSWORD must both be set in $env_file." >&2
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$container"; then
  echo "Container '$container' is not running." >&2
  exit 1
fi

# psql's \set takes the rest of the line verbatim; a single quote inside the
# value would end the literal early, so double it the way SQL expects.
sql_escape() { printf "%s" "${1//\'/\'\'}"; }

echo "Creating roles and database on '$container' ..."
{
  printf "\\set app_password '%s'\n" "$(sql_escape "$app_password")"
  printf "\\set owner_password '%s'\n" "$(sql_escape "$owner_password")"
  cat "$here/create_users_and_db.sql"
} | docker exec -i "$container" psql -v ON_ERROR_STOP=1 -q -U postgres

echo "Granting privileges in jp_conversation ..."
docker exec -i "$container" psql -v ON_ERROR_STOP=1 -q -U postgres -d jp_conversation \
  < "$here/grant_privileges.sql"

echo
echo "Done. The backend creates its tables on first start."
