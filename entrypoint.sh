#!/bin/sh

# Exit immediately if a command exits with a non-zero status
set -e

# Function to wait for postgres
wait_for_postgres() {
    echo "Waiting for PostgreSQL..."
    while ! nc -z "$DB_HOST" "${DB_PORT:-5432}"; do
      sleep 0.1
    done
    echo "PostgreSQL started"
}

# Check if we should wait for DB
if [ "$WAIT_FOR_DB" = "true" ]; then
    wait_for_postgres
fi

# Run migrations if requested
if [ "$RUN_MIGRATIONS" = "true" ]; then
    echo "Running migrations..."
    python manage.py migrate --noinput
fi

# Run collectstatic if requested (usually done in build, but good fallback)
if [ "$RUN_COLLECTSTATIC" = "true" ]; then
    echo "Collecting static files..."
    python manage.py collectstatic --noinput
fi

# Execute the main command
exec "$@"
