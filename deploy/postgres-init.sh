#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname n8n \
  --set=owner="$POSTGRES_USER" <<'SQL'
CREATE DATABASE triage OWNER :"owner";
SQL
