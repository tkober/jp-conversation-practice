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

# Read one value the way docker compose reads an env_file, because the value
# this sets in Postgres has to match exactly what the container later sends.
# Compose strips one surrounding pair of quotes; taking the raw text instead
# would store a password *with* quotes and fail authentication at startup.
read_var() {
  local raw
  # Last occurrence wins, matching how the file would be sourced.
  raw="$(grep -E "^$1=" "$env_file" | tail -n1 | cut -d= -f2-)"

  if [ ${#raw} -ge 2 ]; then
    case "$raw" in
      \"*\"|\'*\') printf '%s' "${raw:1:${#raw}-2}"; return ;;
    esac
  fi
  # Unquoted values lose trailing whitespace, again matching compose.
  printf '%s' "${raw%"${raw##*[![:space:]]}"}"
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

# Prove the credentials actually work rather than assuming they do. This is
# the check that would have caught the quoting bug above: the roles existed and
# the SQL succeeded, but the passwords did not match what the container sends.
verify_login() {
  local user="$1" password="$2"
  docker exec -i -e PGPASSWORD="$password" "$container" \
    psql -h 127.0.0.1 -U "$user" -d jp_conversation -tAc "SELECT 1" >/dev/null 2>&1
}

echo "Verifying both roles can log in ..."
failed=0
for role in "jp_conversation_owner:$owner_password" "jp_conversation_app:$app_password"; do
  user="${role%%:*}"
  password="${role#*:}"
  if verify_login "$user" "$password"; then
    echo "  ok   $user"
  else
    echo "  FAIL $user — the password in $env_file does not match the role" >&2
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  echo >&2
  echo "The backend would fail to start with InvalidPasswordError." >&2
  exit 1
fi

echo
echo "Done. The backend creates its tables on first start."
