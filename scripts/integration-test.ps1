<#
.SYNOPSIS
  Runs the backend live integration tests against the locally installed
  PostgreSQL and Memurai/Redis services (no Docker).

.DESCRIPTION
  Reads DATABASE_URL and REDIS_URL from backend/.env, exposes them to pytest as
  INTEGRATION_DATABASE_URL / INTEGRATION_REDIS_URL for this process only (values
  are never printed), and runs `uv run pytest -m integration`.

  Prerequisite: scripts\setup-local-db.ps1 has been run once.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\integration-test.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\integration-test.ps1 -GitHubLive -CodeforcesLive
  Also runs the opt-in tests against the real GitHub / Codeforces APIs (network).
#>
[CmdletBinding()]
param(
    [switch]$GitHubLive,
    [switch]$CodeforcesLive
)

# Native tools write to stderr; check exit codes explicitly (Windows PowerShell 5.1).
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$envFile = Join-Path $backend ".env"

if (-not (Test-Path $envFile)) {
    throw "Missing backend\.env. Run scripts\setup-local-db.ps1 first."
}

$cfg = @{}
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
        $cfg[$Matches[1]] = $Matches[2].Trim('"').Trim("'")
    }
}
foreach ($key in @("DATABASE_URL", "REDIS_URL")) {
    if (-not $cfg[$key]) { throw "$key is not set in backend\.env" }
}
if ($cfg["DATABASE_URL"] -like "*change-me*") {
    throw "DATABASE_URL still has the placeholder password. Run scripts\setup-local-db.ps1 first."
}

# Quick TCP reachability check so failures are explained before pytest runs.
foreach ($svc in @(@{Name = "PostgreSQL"; Url = $cfg["DATABASE_URL"]}, @{Name = "Redis"; Url = $cfg["REDIS_URL"]})) {
    if ($svc.Url -match '@?(?<host>[A-Za-z0-9.\-]+):(?<port>\d+)/') {
        $ok = Test-NetConnection -ComputerName $Matches.host -Port ([int]$Matches.port) -InformationLevel Quiet -WarningAction SilentlyContinue
        $state = if ($ok) { "reachable" } else { "NOT reachable" }
        Write-Host ("{0,-10} {1}:{2} {3}" -f $svc.Name, $Matches.host, $Matches.port, $state)
    }
}

$env:INTEGRATION_DATABASE_URL = $cfg["DATABASE_URL"]
$env:INTEGRATION_REDIS_URL = $cfg["REDIS_URL"]
# Real-provider LLM tests run only when a Groq key is configured (they cost tokens).
if ($cfg["GROQ_API_KEY"]) {
    $env:INTEGRATION_GROQ_API_KEY = $cfg["GROQ_API_KEY"]
    if ($cfg["LLM_MODEL"]) { $env:INTEGRATION_LLM_MODEL = $cfg["LLM_MODEL"] }
    Write-Host "Groq key found: real-provider LLM tests will run"
} else {
    Write-Host "No GROQ_API_KEY: real-provider LLM tests will be skipped"
}
# Real GitHub API tests are opt-in (network): run with  -GitHubLive
if ($GitHubLive) {
    $env:INTEGRATION_GITHUB_LIVE = "1"
    if ($cfg["GITHUB_TOKEN"]) { $env:INTEGRATION_GITHUB_TOKEN = $cfg["GITHUB_TOKEN"] }
    Write-Host "Real GitHub API tests enabled"
}
# Real Codeforces API tests are opt-in (network, paced): run with  -CodeforcesLive
if ($CodeforcesLive) {
    $env:INTEGRATION_CODEFORCES_LIVE = "1"
    Write-Host "Real Codeforces API tests enabled"
}
Push-Location $backend
try {
    uv run pytest -m integration -v -rs
    $code = $LASTEXITCODE
} finally {
    Pop-Location
    Remove-Item Env:INTEGRATION_DATABASE_URL, Env:INTEGRATION_REDIS_URL, Env:INTEGRATION_GROQ_API_KEY, Env:INTEGRATION_LLM_MODEL, Env:INTEGRATION_GITHUB_LIVE, Env:INTEGRATION_GITHUB_TOKEN, Env:INTEGRATION_CODEFORCES_LIVE -ErrorAction SilentlyContinue
}
exit $code
