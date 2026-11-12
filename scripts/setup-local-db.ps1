<#
.SYNOPSIS
  One-time local database setup (no Docker): creates a least-privilege
  application role and database in the locally installed PostgreSQL.

.DESCRIPTION
  1. Creates backend/.env from backend/.env.example if it does not exist.
  2. If DATABASE_URL still holds the change-me placeholder, generates a random
     password and writes it into backend/.env (the password is never printed).
  3. Connects to PostgreSQL as a superuser (default: postgres). psql prompts for
     that superuser's password itself; this script never sees or stores it.
  4. Idempotently creates/updates role "candidate_app" (LOGIN, NOSUPERUSER,
     NOCREATEDB, NOCREATEROLE) and database "candidate_intel" owned by it.
  5. Verifies the application credentials by connecting as candidate_app.

  Re-running is safe: the role password is reset to the value in backend/.env.

.PARAMETER SuperUser
  PostgreSQL superuser used for setup. Default: postgres.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup-local-db.ps1
#>
[CmdletBinding()]
param(
    [string]$SuperUser = "postgres",
    [string]$PgHost = "127.0.0.1",
    [int]$PgPort = 5432
)

# Native tools write to stderr; check exit codes explicitly (Windows PowerShell 5.1).
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$backendEnv = Join-Path $root "backend\.env"
$backendEnvExample = Join-Path $root "backend\.env.example"

# --- locate psql -------------------------------------------------------------
$psql = (Get-Command psql -ErrorAction SilentlyContinue).Source
if (-not $psql) {
    $psql = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $psql) { throw "psql not found. Install PostgreSQL or add its bin folder to PATH." }

# --- backend/.env --------------------------------------------------------------
if (-not (Test-Path $backendEnv)) {
    Copy-Item $backendEnvExample $backendEnv
    Write-Host "Created backend\.env from backend\.env.example"
}
$lines = Get-Content $backendEnv
$dbLine = $lines | Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -First 1
if (-not $dbLine) { throw "DATABASE_URL not found in backend\.env" }
$dbUrl = ($dbLine -split '=', 2)[1].Trim()

$pattern = '^postgresql\+asyncpg://(?<user>[^:@/]+):(?<pw>[^@]*)@(?<host>[^:/]+):(?<port>\d+)/(?<db>[^?\s]+)'
if ($dbUrl -notmatch $pattern) {
    throw "DATABASE_URL in backend\.env must look like postgresql+asyncpg://user:password@host:port/dbname"
}
# Copy captures now: every later -match overwrites the automatic $Matches variable.
$appUser = $Matches.user
$appPw = $Matches.pw
$appDb = $Matches.db
$urlHost = $Matches.host
$urlPort = $Matches.port

if ($appUser -notmatch '^[a-z_][a-z0-9_]{0,62}$' -or $appDb -notmatch '^[a-z_][a-z0-9_]{0,62}$') {
    throw "Role and database names must be lowercase identifiers (letters, digits, underscore)."
}

if ($appPw -eq "" -or $appPw -like "change-me*") {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    # URL- and SQL-literal-safe: base64url without padding (A-Z a-z 0-9 - _)
    $appPw = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    $newUrl = "postgresql+asyncpg://${appUser}:${appPw}@${urlHost}:${urlPort}/${appDb}"
    $lines = $lines | ForEach-Object { if ($_ -match '^\s*DATABASE_URL\s*=') { "DATABASE_URL=$newUrl" } else { $_ } }
    Set-Content -Path $backendEnv -Value $lines -Encoding ascii
    Write-Host "Generated a new random password for '$appUser' and saved it in backend\.env"
}
if ($appPw -notmatch '^[A-Za-z0-9._~-]+$') {
    throw "The DATABASE_URL password must be URL-safe (letters, digits, '.', '_', '~', '-')."
}

# --- create role + database (SQL sent via stdin, not the command line) -------
$sql = @"
\set ON_ERROR_STOP on
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$appUser') THEN
    CREATE ROLE $appUser LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
END
`$`$;
ALTER ROLE $appUser WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '$appPw';
SELECT format('CREATE DATABASE %I OWNER %I ENCODING ''UTF8''', '$appDb', '$appUser')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '$appDb') \gexec
ALTER DATABASE $appDb OWNER TO $appUser;
REVOKE ALL ON DATABASE $appDb FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE $appDb TO $appUser;
"@

Write-Host "Connecting to PostgreSQL at ${PgHost}:${PgPort} as '$SuperUser' (psql will prompt for its password)..."
$sql | & $psql -h $PgHost -p $PgPort -U $SuperUser -d postgres -q -X
if ($LASTEXITCODE -ne 0) { throw "Database setup failed (psql exit code $LASTEXITCODE)." }

# --- verify app credentials ----------------------------------------------------
$env:PGPASSWORD = $appPw
try {
    $who = & $psql -h $PgHost -p $PgPort -U $appUser -d $appDb -w -X -Atc "select current_user || ' on ' || current_database()"
    if ($LASTEXITCODE -ne 0) { throw "Could not connect as '$appUser'." }
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
Write-Host "OK: connected as $who"
Write-Host "Next: cd backend; uv run alembic upgrade head; then run scripts\integration-test.ps1"
