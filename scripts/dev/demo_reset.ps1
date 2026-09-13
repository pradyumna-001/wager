# One-command demo reset: DB check + migrate + seed bets + PostHog events (M6.2).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

if (-not (Test-Path ".env")) {
    Write-Error "error: .env not found at repo root (see .env.example)"
}

Get-Content .env |
    Where-Object { $_ -match "^\s*([^#=\s]+)\s*=\s*(.*)\s*$" } |
    ForEach-Object {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"'), "Process")
    }

Write-Host "[demo] checking DB connectivity..."
python -c "from pm_agent.db.connection import get_conn
try:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1')
            cur.fetchone()
except Exception as e:
    raise SystemExit('error: DATABASE_URL is not reachable - fix the .env credentials and start Postgres first (%s: %s)' % (type(e).__name__, e))"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[demo] migrating..."
python scripts/migrate.py
Write-Host "[demo] seeding bets (S1-S3 + D1)..."
python scripts/seed_demo.py --reset
Write-Host "[demo] pushing PostHog events + verifying lifts..."
python scripts/seed_posthog.py --verify
Write-Host "[demo] reset complete - run the demo per docs/08_DEMO_SCRIPT.md"
