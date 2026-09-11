# SUPERSEDED by phase1-continue.ps1 (kept for history).
# The phase1-continue.ps1 script is the single source of truth for steps C..J and
# validates pytest via --junitxml + exit code (this script's earlier regex-based
# validation produced a false positive because PowerShell 5.1 loses part of the
# native pytest output when merging streams).
param([switch]$Resume)

$ErrorActionPreference = "Stop"
$root = "D:\ai"
$bk = "$root\backend"
$log = "$root\phase1.log"
$stateFile = "$root\.jobfit_phase1_state.json"
$venvPy = "$bk\.venv\Scripts\python.exe"
$scriptPath = $MyInvocation.MyCommand.Path

function Log([string]$m) {
    $line = "[$(Get-Date -Format o)] $m"
    try { Add-Content -LiteralPath $log -Value $line -Encoding UTF8 } catch {}
    Write-Host $line
}

function SaveState($status, [string[]]$completed, [bool]$restartReq) {
    $obj = [ordered]@{ phase = "phase1_env"; status = $status; completed = @($completed); restart_requested = $restartReq }
    $obj | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $stateFile -Encoding UTF8
}

function Get-Completed() {
    if (Test-Path $stateFile) {
        try { $s = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json; if ($s.completed) { return @($s.completed) } } catch {}
    }
    return @()
}

function SaveProgress([string]$status) {
    SaveState $status @($global:phase1_done) $false
}

function Halt-Blocked([string]$stage, [string]$cmd, [string]$expected, [string]$actual, [string]$action) {
    $blk = [ordered]@{
        phase = "phase1_env"; status = "BLOCKED"; stage = $stage
        blocker = $stage; expected = $expected; actual = $actual
        failed_command = $cmd; required_action = $action
        completed = @($global:phase1_done)
    }
    $blk | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stateFile -Encoding UTF8
    Log "GATE=BLOCKED stage=$stage actual=$actual"
    exit 33
}

function Test-WslReady() {
    try {
        & wsl.exe --status *> $null
        if ($LASTEXITCODE -eq 0) {
            & wsl.exe --version *> $null
            if ($LASTEXITCODE -eq 0) { return $true }
        }
    } catch {}
    return $false
}

# ---- load state ----
$completed = Get-Completed
$done = @{}
foreach ($c in $completed) { $done[$c] = $true }
$global:phase1_done = @($done.Keys)

# ---- decide elevation: only needed when WSL is still missing ----
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
$isAdmin = $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$wslReadyNow = Test-WslReady
if (-not $isAdmin -and -not $wslReadyNow) {
    Log "Not admin and WSL missing. Self-elevating with UAC..."
    try {
        Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$scriptPath`"", "-Resume") -Verb RunAs -Wait
    } catch {
        Halt-Blocked "elevation" "Start-Process -Verb RunAs" "admin session" "UAC canceled: $($_.Exception.Message)" "Click Yes on the UAC prompt (or double-click D:\ai\run-phase1.cmd), then rerun."
    }
    exit 0
}
if ($isAdmin) { Log "== phase1-run running as Administrator (Resume=$Resume) ==" }
else { Log "== phase1-run running WITHOUT admin (WSL already present; Docker Desktop is per-user) ==" }

# ------------------------------------------------------------------ WSL
if (-not $done["WSL"]) {
    Log "STEP A: WSL check/install"
    if (Test-WslReady) {
        Log "WSL already available."
        $done["WSL"] = $true
    } else {
        Log "Installing WSL (--install --no-distribution) ..."
        & wsl.exe --install --no-distribution 2>&1 | ForEach-Object { Log "wsl-install: $_" }
        & wsl.exe --update 2>&1 | ForEach-Object { Log "wsl-update: $_" }
        if (-not (Test-WslReady)) {
            # Feature install usually needs a Windows restart before WSL reports ready.
            try {
                & schtasks.exe /Create /F /TN "JobFitPhase1Resume" /SC ONLOGON /RL HIGHEST /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -Resume" | Out-Null
            } catch { Log "schtasks register failed: $_" }
            $global:phase1_done = @($done.Keys)
            SaveState "RESTART_REQUIRED" @($done.Keys) $true
            Log "ENVIRONMENT PAUSED - RESTART REQUIRED"
            exit 0
        }
        $done["WSL"] = $true
        Log "WSL available after install."
    }
    $global:phase1_done = @($done.Keys)
    SaveProgress "RUNNING"
}

# ------------------------------------------------------------------ Docker Desktop
if (-not $done["Docker"]) {
    Log "STEP B: Docker Desktop install/start"
    function Resolve-DockerCli {
        $cands = @(
            "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe",
            "$env:LOCALAPPDATA\Docker\Docker\resources\bin\docker.exe"
        )
        foreach ($c in $cands) { if (Test-Path $c) { return $c } }
        $cmd = Get-Command docker -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
        return $null
    }
    $dockerCli = Resolve-DockerCli
    if (-not $dockerCli) {
        Log "Installing Docker Desktop via winget (silent) ..."
        & winget.exe install --exact --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements --disable-interactivity 2>&1 | ForEach-Object { Log "winget: $_" }
        $dockerCli = Resolve-DockerCli
    }
    if (-not $dockerCli) {
        Halt-Blocked "docker" "winget install Docker.DockerDesktop" "docker.exe present" "docker.exe not found after install" "Check winget output in phase1.log; rerun."
    }
    $cliDir = Split-Path $dockerCli
    $env:Path = "$cliDir;$env:Path"
    foreach ($p in @("$env:ProgramFiles\Docker\cli-plugins", "$env:LOCALAPPDATA\Docker\cli-plugins")) {
        if (Test-Path $p) { $env:Path = "$p;$env:Path" }
    }
    Log "docker cli resolved: $dockerCli"
    docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        Halt-Blocked "docker-compose" "docker compose version" "compose plugin available" "compose plugin missing" "Reinstall/repair Docker Desktop, then rerun."
    }
    Log "docker compose plugin available"
    $candidates = @(
        "$env:LOCALAPPDATA\Docker\Docker Desktop.exe",
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    )
    $exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $exe) { $exe = (Get-Command "Docker Desktop" -ErrorAction SilentlyContinue).Source }
    if (-not $exe) {
        Log "Docker Desktop exe not found in standard paths; trying start-menu search."
        $exe = (Get-ChildItem "$env:LOCALAPPDATA\Docker" -Recurse -Filter "Docker Desktop.exe" -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
    }
    if (-not $exe) {
        Halt-Blocked "docker" "start Docker Desktop" "Docker Desktop exe" "not found" "Install Docker Desktop and start it once; accept any license dialog."
    }
    $proc = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
    if (-not $proc) {
        Log "Starting Docker Desktop: $exe"
        Start-Process -FilePath $exe
    }
    # wait for engine
    $engineOk = $false
    for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep -Seconds 5
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { $engineOk = $true; break }
    }
    if (-not $engineOk) {
        Halt-Blocked "docker-engine" "docker info" "engine ready" "engine not ready after ~10min" "Open Docker Desktop and accept any first-run/license/WSL dialog, then rerun."
    }
    $osType = (docker info --format '{{.OSType}}' 2>$null).Trim()
    if ($osType -ne "linux") {
        Log "OSType=$osType; switching engine to linux"
        & docker desktop engine use linux 2>&1 | ForEach-Object { Log "engine: $_" }
        Start-Sleep -Seconds 15
        $osType = (docker info --format '{{.OSType}}' 2>$null).Trim()
    }
    if ($osType -ne "linux") {
        Halt-Blocked "docker-linux" "docker info --format OSType" "linux" $osType "Switch Docker Desktop to Linux containers, then rerun."
    }
    Log "Docker engine ready, OSType=linux"
    $done["Docker"] = $true
    $global:phase1_done = @($done.Keys)
    SaveProgress "RUNNING"
}

# ------------------------------------------------------------------ project config files
Log "STEP C: project config (compose/env/gitignore)"
$compose = "$bk\docker-compose.yml"
if (-not (Test-Path $compose)) {
    @'
services:
  db:
    image: pgvector/pgvector:pg16
    container_name: jobfit-pg
    restart: unless-stopped
    environment:
      POSTGRES_USER: jobfit
      POSTGRES_PASSWORD: jobfit
      POSTGRES_DB: jobfit
    ports:
      - "5432:5432"
    volumes:
      - jobfit_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U jobfit -d jobfit"]
      interval: 5s
      timeout: 5s
      retries: 30

volumes:
  jobfit_pgdata:
'@ | Set-Content -LiteralPath $compose -Encoding UTF8
    Log "compose file created: $compose"
}

$envPath = "$bk\.env"
if (-not (Test-Path $envPath)) {
    Log "Creating backend/.env (local dev db only)"
    @'
POSTGRES_USER=jobfit
POSTGRES_PASSWORD=jobfit
POSTGRES_DB=jobfit
DATABASE_URL=postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit
TEST_DATABASE_URL=postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit_test
'@ | Set-Content -LiteralPath $envPath -Encoding UTF8
} else {
    Log "backend/.env exists; merging missing DB keys only (secrets preserved)"
    $lines = @(Get-Content -LiteralPath $envPath -Encoding UTF8)
    $existing = @{}
    foreach ($l in $lines) { if ($l -match '^\s*([^#=]+)=') { $existing[$matches[1].Trim()] = $true } }
    $toAdd = @()
    $adds = @{ "POSTGRES_USER"="jobfit"; "POSTGRES_PASSWORD"="jobfit"; "POSTGRES_DB"="jobfit";
               "DATABASE_URL"="postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit";
               "TEST_DATABASE_URL"="postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit_test" }
    foreach ($k in $adds.Keys) { if (-not $existing[$k]) { $toAdd += "$k=$($adds[$k])" } }
    if ($toAdd.Count -gt 0) { Add-Content -LiteralPath $envPath -Value $toAdd -Encoding UTF8 }
}

$gi = "$bk\.gitignore"
if (-not (Test-Path $gi)) {
    @'
.env
*.env
__pycache__/
.venv/
data/
*.pyc
'@ | Set-Content -LiteralPath $gi -Encoding UTF8
    Log "backend/.gitignore created"
}

# ------------------------------------------------------------------ postgres container
Log "STEP D: start jobfit-pg"
Set-Location $bk
& docker compose up -d db 2>&1 | ForEach-Object { Log "compose-up: $_" }

$healthy = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 5
    $st = docker inspect -f '{{.State.Health.Status}}' jobfit-pg 2>$null
    if ($st -eq "healthy") { $healthy = $true; break }
    if ($st -eq "unhealthy") { break }
}
if (-not $healthy) {
    Halt-Blocked "postgres-container" "docker compose up db / healthcheck" "jobfit-pg healthy" "not healthy" "Inspect with: docker logs jobfit-pg ; then rerun."
}
Log "jobfit-pg healthy"

# pgvector on jobfit db
docker exec jobfit-pg psql -U jobfit -d jobfit -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>&1 | ForEach-Object { Log "ext: $_" }
$vec = docker exec jobfit-pg psql -U jobfit -d jobfit -tAc "SELECT extname FROM pg_extension WHERE extname='vector';" 2>$null
if ("$vec" -notmatch "vector") {
    Halt-Blocked "pgvector" "CREATE EXTENSION vector" "vector present" "$vec" "Check docker logs jobfit-pg; image must be pgvector/pgvector."
}
Log "pgvector present on jobfit"

# test database
$has = docker exec jobfit-pg psql -U jobfit -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='jobfit_test';" 2>$null
if (("$has").Trim() -ne "1") {
    docker exec jobfit-pg psql -U jobfit -d postgres -c "CREATE DATABASE jobfit_test OWNER jobfit;" 2>&1 | ForEach-Object { Log "createdb: $_" }
}
docker exec jobfit-pg psql -U jobfit -d jobfit_test -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>&1 | ForEach-Object { Log "test-ext: $_" }
$vecTest = docker exec jobfit-pg psql -U jobfit -d jobfit_test -tAc "SELECT extname FROM pg_extension WHERE extname='vector';" 2>$null
if ("$vecTest" -notmatch "vector") {
    Halt-Blocked "test-db-pgvector" "CREATE EXTENSION vector on jobfit_test" "vector present" "$vecTest" "Create test db manually and enable vector."
}
Log "test database + pgvector ready"
$done["DB"] = $true
$global:phase1_done = @($done.Keys)
SaveProgress "RUNNING"

# ------------------------------------------------------------------ alembic round-trip
Log "STEP E: alembic migration (real DB)"
$env:DATABASE_URL = "postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit"
$env:TEST_DATABASE_URL = "postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit_test"

& $venvPy -m alembic current 2>&1 | ForEach-Object { Log "alembic current(initial): $_" }
& $venvPy -m alembic upgrade head 2>&1 | ForEach-Object { Log "alembic upgrade: $_" }
if ($LASTEXITCODE -ne 0) {
    Halt-Blocked "alembic-upgrade" "alembic upgrade head" "exit 0" "exit $LASTEXITCODE" "Fix migration/report real error."
}
& $venvPy -m alembic current 2>&1 | ForEach-Object { Log "alembic current(after upgrade): $_" }
$curHead = (& $venvPy -m alembic current 2>&1 | Out-String)
if ($curHead -notmatch "0001_baseline") {
    Halt-Blocked "alembic-head" "alembic current" "0001_baseline (head)" $curHead "Inspect alembic state."
}
Log "alembic upgrade head OK (0001_baseline)"

$tables = docker exec jobfit-pg psql -U jobfit -d jobfit -tAc "SELECT string_agg(table_name, ',') FROM information_schema.tables WHERE table_schema='public';" 2>$null
Log "tables: $tables"
foreach ($t in @("documents","parsed_documents","document_chunks","resume_profiles","jd_profiles","analyses","llm_attempt_log","decision_traces","reports","job_runs","audit_log")) {
    if ($tables -notmatch $t) { Halt-Blocked "schema-table" "information_schema tables" $t "missing: $t" "Inspect migration." }
}
$colType = docker exec jobfit-pg psql -U jobfit -d jobfit -tAc "SELECT udt_name FROM information_schema.columns WHERE table_name='document_chunks' AND column_name='embedding';" 2>$null
if ($colType.Trim() -ne "vector") {
    Halt-Blocked "vector-type" "column udt check" "vector" $colType "pgvector type not applied."
}
Log "embedding column type = $colType"

& $venvPy -m alembic downgrade base 2>&1 | ForEach-Object { Log "alembic downgrade: $_" }
if ($LASTEXITCODE -ne 0) { Halt-Blocked "alembic-downgrade" "alembic downgrade base" "exit 0" "exit $LASTEXITCODE" "Fix migration downgrade." }
Log "alembic downgrade base OK"
& $venvPy -m alembic upgrade head 2>&1 | ForEach-Object { Log "alembic re-upgrade: $_" }
if ($LASTEXITCODE -ne 0) { Halt-Blocked "alembic-reupgrade" "alembic upgrade head again" "exit 0" "exit $LASTEXITCODE" "Fix migration." }
Log "alembic re-upgrade OK"
$done["Alembic"] = $true
$global:phase1_done = @($done.Keys)
SaveProgress "RUNNING"

# ------------------------------------------------------------------ integration tests
Log "STEP F: integration tests (real DB)"
$itOut = (& $venvPy -m pytest tests/integration -q -rs --tb=short 2>&1 | Out-String)
$itOut | ForEach-Object { Log "it: $_" }
if ($itOut -match "database unavailable") {
    Halt-Blocked "integration-skip" "pytest tests/integration" "no DB skips" "skipped: database unavailable" "Ensure DATABASE_URL/TEST_DATABASE_URL set and container healthy."
}
$itOk = $false
if ($itOut -match "passed" -and $itOut -notmatch "failed|error") { $itOk = $true }
if (-not $itOk) { Halt-Blocked "integration-fail" "pytest tests/integration" "all passed" "see log" "Fix real failures." }
Log "integration tests OK"

# ------------------------------------------------------------------ full pytest
Log "STEP G: full pytest"
$fullOut = (& $venvPy -m pytest -q -rs --tb=short 2>&1 | Out-String)
$fullOut | ForEach-Object { Log "pytest: $_" }
$fullOk = $false
if ($fullOut -match "passed") {
    $skippedMatch = [regex]::Match($fullOut, "([0-9]+) skipped")
    $skipped = if ($skippedMatch.Success) { [int]$skippedMatch.Groups[1].Value } else { 0 }
    if ($fullOut -notmatch "failed|error" -and $skipped -eq 0 -and $fullOut -notmatch "database unavailable") { $fullOk = $true }
    Log "pytest skipped count = $skipped"
}
if (-not $fullOk) { Halt-Blocked "pytest" "pytest -q" "all pass, 0 skipped" "see log" "Fix real failures." }

# ------------------------------------------------------------------ ruff / mypy
Log "STEP H: ruff"
$ruffOut = (& $venvPy -m ruff check src tests alembic 2>&1 | Out-String)
$ruffOut | ForEach-Object { Log "ruff: $_" }
if ($ruffOut -notmatch "All checks passed") { Halt-Blocked "ruff" "ruff check" "All checks passed" "failures" "Fix lint." }

Log "STEP I: mypy"
$mypyOut = (& $venvPy -m mypy src/jobfit tests 2>&1 | Out-String)
$mypyOut | ForEach-Object { Log "mypy: $_" }
if ($mypyOut -notmatch "Success: no issues found") { Halt-Blocked "mypy" "mypy src tests" "Success" "issues" "Fix types." }

# ------------------------------------------------------------------ scans
Log "STEP J: lease/fencing/transaction scan"
$nowHits = Get-ChildItem "$bk\src" -Recurse -Filter *.py | Select-String -Pattern 'now\(\)' | Where-Object { $_.Line -notmatch 'clock_timestamp|#|now\(\) 不|不使用 now' }
foreach ($h in $nowHits) { Log "now-hit: $($h.Path):$($h.LineNumber): $($h.Line.Trim())" }
$todoHits = Get-ChildItem "$bk\src" -Recurse -Filter *.py | Select-String -Pattern 'TODO|NotImplementedError'
foreach ($h in $todoHits) { Log "todo-hit: $($h.Path):$($h.LineNumber): $($h.Line.Trim())" }
$blockers = @()
foreach ($h in $nowHits) { $blockers += "now() in src lease/fencing logic: $($h.Path):$($h.LineNumber)" }
foreach ($h in $todoHits) { $blockers += "TODO/NotImplemented in src: $($h.Path):$($h.LineNumber)" }
if ($blockers.Count -gt 0) {
    $blk = [ordered]@{ phase="phase1_env"; status="BLOCKED"; stage="scan"; blocker=($blockers -join "; "); expected="none"; actual=($blockers -join "; "); failed_command="scan"; required_action="Fix and rerun" }
    $blk | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stateFile -Encoding UTF8
    Log "GATE=BLOCKED stage=scan"
    exit 34
}

# done
$final = [ordered]@{
    phase = "phase1_env"; status = "COMPLETE"
    wsl = "ok"; docker_linux = "ok"; postgres = "healthy"; pgvector = "ok"
    alembic = "round-trip ok"; integration = "real"; pytest = "pass"; ruff = "pass"; mypy = "pass"; skipped_db = 0
}
$final | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $stateFile -Encoding UTF8
try { schtasks.exe /Delete /F /TN "JobFitPhase1Resume" | Out-Null } catch {}
Log "GATE=COMPLETE"
Log "PHASE1_PIPELINE_DONE"
exit 0
