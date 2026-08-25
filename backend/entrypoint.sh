#!/usr/bin/env sh
set -e

# Wait for Postgres to accept connections before starting the app.
if [ -n "$DATABASE_URL" ]; then
  echo "Waiting for the database…"
  python - <<'PY'
import os, time, sys
from urllib.parse import urlparse
import psycopg2

url = urlparse(os.environ["DATABASE_URL"])
for attempt in range(60):
    try:
        psycopg2.connect(
            dbname=url.path.lstrip("/"), user=url.username, password=url.password,
            host=url.hostname or "localhost", port=url.port or 5432,
        ).close()
        print("Database is up.")
        break
    except Exception as e:
        print(f"  …not ready ({e}); retrying")
        time.sleep(1)
else:
    print("Database never became available.", file=sys.stderr)
    sys.exit(1)
PY
fi

# Ensure media subdirectories exist and are writable by appuser.
mkdir -p /app/media/datasets /app/media/payment_proofs /app/media/address_proofs
chown -R appuser:appuser /app/media

# The app's startup hook runs create_all + seed (RBAC + admin), all idempotent.
exec su -p appuser -c "exec $*"
