#!/bin/bash
set -e

# Wait for PostgreSQL to be ready
until pg_isready -U postgres; do
    sleep 1
done

# Grant CREATEDB privilege to the postgres superuser
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
  ALTER USER postgres CREATEDB;
EOSQL

echo "Tenant setup complete - postgres user now has CREATEDB privilege"
