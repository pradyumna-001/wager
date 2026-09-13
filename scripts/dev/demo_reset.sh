#!/usr/bin/env bash
# One-command demo reset: DB check + migrate + seed bets + PostHog events (M6.2).
set -euo pipefail
cd "$(dirname "$0")/../.."

if [ ! -f .env ]; then
  echo "error: .env not found at repo root (see .env.example)" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
. ./.env
set +a

echo "[demo] checking DB connectivity..."
python - <<'PY'
from pm_agent.db.connection import get_conn

try:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
except Exception as e:
    raise SystemExit(
        "error: DATABASE_URL is not reachable — fix the .env credentials and start "
        f"Postgres first ({type(e).__name__}: {e})"
    )
print("[demo] DB OK")
PY

echo "[demo] migrating..."
python scripts/migrate.py
echo "[demo] seeding bets (S1-S3 + D1)..."
python scripts/seed_demo.py --reset
echo "[demo] pushing PostHog events + verifying lifts..."
python scripts/seed_posthog.py --verify
echo "[demo] reset complete — run the demo per docs/08_DEMO_SCRIPT.md"
