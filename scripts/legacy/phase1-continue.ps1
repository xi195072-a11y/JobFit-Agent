$ErrorActionPreference = "Continue"
$root = "D:\ai"
$bk = "$root\backend"
$log = "$root\phase1.log"
$stateFile = "$root\.jobfit_phase1_state.json"
$venvPy = "$bk\.venv\Scripts\python.exe"
$dockerExe = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"

function Log([string]$m) {
    $line = "[$(Get-Date -Format o)] $m"
    try { Add-Content -LiteralPath $log -Value $line -Encoding UTF8 } catch {}
    Write-Host $line
}

function SaveState([string]$status, [string[]]$completed) {
    $obj = [ordered]@{ phase = "phase1_env"; status = $status; completed = @($completed); restart_requested = $false }
    $obj | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $stateFile -Encoding UTF8
}

$done = @()
function Mark([string]$name) {
    if ($done -notcontains $name) { $script:done += $name }
    SaveState "RUNNING" $script:done
}

function Fail([string]$stage, [string]$cmd, [string]$expected, [string]$actual, [string]$action) {
    $obj = [ordered]@{
        phase = "phase1_env"; status = "BLOCKED"; stage = $stage; blocker = $stage
        expected = $expected; actual = $actual; failed_command = $cmd
        required_action = $action; completed = @($script:done)
    }
    $obj | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stateFile -Encoding UTF8
    Log "GATE=BLOCKED stage=$stage actual=$actual"
    exit 33
}

Log "== phase1-continue started =="

$env:Path = (Split-Path $dockerExe) + ";" + $env:Path
foreach ($p in @("$env:ProgramFiles\Docker\cli-plugins", "$env:LOCALAPPDATA\Docker\cli-plugins")) {
    if (Test-Path $p) { $env:Path = "$p;$env:Path" }
}
$env:DATABASE_URL = "postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit"
$env:TEST_DATABASE_URL = "postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit_test"

# ---- engine precondition ----
$ver = (& $dockerExe info --format '{{.ServerVersion}} {{.OSType}}' 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $ver -notmatch "linux") {
    Fail "docker-engine" "docker info" "linux engine" "$ver" "Start Docker Desktop and wait for engine ready, then rerun."
}
Log "engine ok: $ver"

# ---- STEP C: project config ----
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
    Log "created: $compose"
}

$envPath = "$bk\.env"
if (-not (Test-Path $envPath)) {
    @'
POSTGRES_USER=jobfit
POSTGRES_PASSWORD=jobfit
POSTGRES_DB=jobfit
DATABASE_URL=postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit
TEST_DATABASE_URL=postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit_test
'@ | Set-Content -LiteralPath $envPath -Encoding UTF8
    Log "created: $envPath"
} else {
    $lines = @(Get-Content -LiteralPath $envPath -Encoding UTF8)
    $existing = @{}
    foreach ($l in $lines) { if ($l -match '^\s*([^#=]+)=') { $existing[$matches[1].Trim()] = $true } }
    $adds = [ordered]@{
        POSTGRES_USER = "jobfit"; POSTGRES_PASSWORD = "jobfit"; POSTGRES_DB = "jobfit"
        DATABASE_URL = "postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit"
        TEST_DATABASE_URL = "postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit_test"
    }
    $toAdd = @()
    foreach ($k in $adds.Keys) { if (-not $existing[$k]) { $toAdd += "$k=$($adds[$k])" } }
    if ($toAdd.Count -gt 0) { Add-Content -LiteralPath $envPath -Value $toAdd -Encoding UTF8 }
    Log "merged missing db keys into existing .env (secrets preserved)"
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
    Log "created: $gi"
}
Mark "CONFIG"

# ---- STEP D: postgres container ----
Set-Location $bk
(& $dockerExe compose up -d db 2>&1) | ForEach-Object { Log "compose: $_" }

$healthy = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 5
    $st = (& $dockerExe inspect -f '{{.State.Health.Status}}' jobfit-pg 2>$null | Out-String).Trim()
    if ($st -eq "healthy") { $healthy = $true; break }
    if ($st -eq "unhealthy") { break }
}
if (-not $healthy) {
    (& $dockerExe logs --tail 30 jobfit-pg 2>&1) | ForEach-Object { Log "pg-log: $_" }
    Fail "postgres-container" "docker compose up -d db" "jobfit-pg healthy" "not healthy" "Inspect docker logs jobfit-pg."
}
Log "jobfit-pg healthy"

(& $dockerExe exec jobfit-pg psql -U jobfit -d jobfit -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>&1) | ForEach-Object { Log "ext: $_" }
$vec = (& $dockerExe exec jobfit-pg psql -U jobfit -d jobfit -tAc "SELECT extname FROM pg_extension WHERE extname='vector';" 2>$null | Out-String).Trim()
if ($vec -ne "vector") { Fail "pgvector" "CREATE EXTENSION vector" "vector" "$vec" "Check image is pgvector/pgvector:pg16." }
Log "pgvector present: $vec"

$has = (& $dockerExe exec jobfit-pg psql -U jobfit -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='jobfit_test';" 2>$null | Out-String).Trim()
if ($has -ne "1") {
    (& $dockerExe exec jobfit-pg psql -U jobfit -d postgres -c "CREATE DATABASE jobfit_test OWNER jobfit;" 2>&1) | ForEach-Object { Log "createdb: $_" }
}
(& $dockerExe exec jobfit-pg psql -U jobfit -d jobfit_test -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>&1) | ForEach-Object { Log "test-ext: $_" }
$vecTest = (& $dockerExe exec jobfit-pg psql -U jobfit -d jobfit_test -tAc "SELECT extname FROM pg_extension WHERE extname='vector';" 2>$null | Out-String).Trim()
if ($vecTest -ne "vector") { Fail "test-db" "CREATE EXTENSION vector on jobfit_test" "vector" "$vecTest" "Create jobfit_test and enable vector." }
Log "test db ok: $vecTest"
Mark "DB"

# ---- STEP E: alembic round-trip ----
(& $venvPy -m alembic current 2>&1) | ForEach-Object { Log "alembic current(before): $_" }
(& $venvPy -m alembic upgrade head 2>&1) | ForEach-Object { Log "alembic upgrade: $_" }
if ($LASTEXITCODE -ne 0) { Fail "alembic-upgrade" "alembic upgrade head" "exit 0" "exit $LASTEXITCODE" "Fix migration error." }
$cur = (& $venvPy -m alembic current 2>&1 | Out-String)
if ($cur -notmatch "0001_baseline") { Fail "alembic-head" "alembic current" "0001_baseline" "$cur" "Inspect alembic state." }
Log "alembic head = 0001_baseline"

$tables = (& $dockerExe exec jobfit-pg psql -U jobfit -d jobfit -tAc "SELECT string_agg(table_name, ',') FROM information_schema.tables WHERE table_schema='public';" 2>$null | Out-String).Trim()
Log "tables: $tables"
foreach ($t in @("documents","parsed_documents","document_chunks","resume_profiles","resume_education","resume_experiences","resume_skills","jd_profiles","jd_requirements","analyses","hard_constraint_results","skill_match_results","decision_traces","score_snapshots","critiques","reports","reviews","job_runs","llm_attempt_log","audit_log")) {
    if ($tables -notmatch $t) { Fail "schema" "information_schema.tables" $t "missing $t" "Inspect migration." }
}
$vecType = (& $dockerExe exec jobfit-pg psql -U jobfit -d jobfit -tAc "SELECT udt_name FROM information_schema.columns WHERE table_name='document_chunks' AND column_name='embedding';" 2>$null | Out-String).Trim()
if ($vecType -ne "vector") { Fail "vector-type" "document_chunks.embedding" "vector" "$vecType" "pgvector type not applied." }
Log "embedding column type = $vecType"

$uniq = (& $dockerExe exec jobfit-pg psql -U jobfit -d jobfit -tAc "SELECT conname FROM pg_constraint WHERE conname IN ('uq_parsed_document_version','uq_document_chunk_index','uq_resume_profile_fingerprint','uq_jd_profile_fingerprint','uq_llm_attempt_log','uq_analyses_idempotency_key');" 2>$null | Out-String).Trim()
Log "unique constraints found: $uniq"
foreach ($c in @("uq_parsed_document_version","uq_document_chunk_index","uq_resume_profile_fingerprint","uq_jd_profile_fingerprint","uq_llm_attempt_log")) {
    if ($uniq -notmatch $c) { Fail "schema-unique" "pg_constraint" $c "missing $c" "Inspect migration." }
}

(& $venvPy -m alembic downgrade base 2>&1) | ForEach-Object { Log "alembic downgrade: $_" }
if ($LASTEXITCODE -ne 0) { Fail "alembic-downgrade" "alembic downgrade base" "exit 0" "exit $LASTEXITCODE" "Fix downgrade." }
Log "alembic downgrade base OK"
(& $venvPy -m alembic upgrade head 2>&1) | ForEach-Object { Log "alembic re-upgrade: $_" }
if ($LASTEXITCODE -ne 0) { Fail "alembic-reupgrade" "alembic upgrade head" "exit 0" "exit $LASTEXITCODE" "Fix migration." }
Log "alembic re-upgrade OK (round-trip complete)"
Mark "Alembic"

# ---- STEP F: integration tests ----
$itXml = "$root\_it.xml"
$itTxt = "$root\pytest-integration.txt"
& $venvPy -m pytest tests/integration -q -rs --tb=short --junitxml $itXml *> $itTxt
$itCode = $LASTEXITCODE
$itOut = Get-Content -Raw -LiteralPath $itTxt
Log "integration exit=$itCode"
$itOut | ForEach-Object { Log ("integration-out: " + $_) }
$itStats = $null
if (Test-Path $itXml) {
    [xml]$itDoc = Get-Content -Raw -LiteralPath $itXml
    $itStats = $itDoc.SelectNodes('//testsuite') | Select-Object -First 1
}
if ($null -eq $itStats) {
    Fail "integration-report" "pytest --junitxml" "junit xml produced" "missing/empty xml" "Fix pytest invocation."
}
$itStatsLine = "tests=$($itStats.tests) failures=$($itStats.failures) errors=$($itStats.errors) skipped=$($itStats.skipped)"
Log "integration stats: $itStatsLine"
if ($itCode -ne 0 -or [int]$itStats.failures -gt 0 -or [int]$itStats.errors -gt 0 -or [int]$itStats.skipped -gt 0 -or [int]$itStats.tests -lt 13) {
    Fail "integration" "pytest tests/integration" "13 tests, 0 failed/0 errors/0 skipped" $itStatsLine "Fix real failures; DB skips not allowed."
}
Log "integration tests executed: $itStatsLine"
Mark "Integration"

# ---- STEP G: full pytest ----
$fullXml = "$root\_full.xml"
$fullTxt = "$root\pytest-full.txt"
& $venvPy -m pytest -q -rs --tb=short --junitxml $fullXml *> $fullTxt
$fullCode = $LASTEXITCODE
$fullOut = Get-Content -Raw -LiteralPath $fullTxt
Log "pytest exit=$fullCode"
$fullOut | ForEach-Object { Log ("pytest-out: " + $_) }
$fullStats = $null
if (Test-Path $fullXml) {
    [xml]$fullDoc = Get-Content -Raw -LiteralPath $fullXml
    $fullStats = $fullDoc.SelectNodes('//testsuite') | Select-Object -First 1
}
if ($null -eq $fullStats) {
    Fail "pytest-report" "pytest --junitxml" "junit xml produced" "missing/empty xml" "Fix pytest invocation."
}
$fullStatsLine = "tests=$($fullStats.tests) failures=$($fullStats.failures) errors=$($fullStats.errors) skipped=$($fullStats.skipped)"
Log "full pytest stats: $fullStatsLine"
if ($fullCode -ne 0 -or [int]$fullStats.failures -gt 0 -or [int]$fullStats.errors -gt 0 -or [int]$fullStats.skipped -gt 0 -or [int]$fullStats.tests -lt 57) {
    Fail "pytest" "pytest -q" "57 tests, 0 failed/0 errors/0 skipped" $fullStatsLine "Fix real failures; no skips allowed."
}
Log "full pytest executed: $fullStatsLine"
Mark "Pytest"

# ---- STEP H: ruff ----
$ruffOut = (& $venvPy -m ruff check src tests alembic 2>&1 | Out-String)
$ruffOut | ForEach-Object { Log "ruff: $_" }
if ($ruffOut -notmatch "All checks passed") { Fail "ruff" "ruff check" "All checks passed" "lint failures" "Fix lint." }
Log "ruff passed"
Mark "Ruff"

# ---- STEP I: mypy ----
$mypyOut = (& $venvPy -m mypy src/jobfit tests 2>&1 | Out-String)
$mypyOut | ForEach-Object { Log "mypy: $_" }
if ($mypyOut -notmatch "Success: no issues found") { Fail "mypy" "mypy src tests" "Success" "type issues" "Fix types." }
Log "mypy passed"
Mark "Mypy"

# ---- STEP J: scans ----
$nowHits = Get-ChildItem "$bk\src" -Recurse -Filter *.py | Select-String -Pattern 'now\(\)' | Where-Object { $_.Line -notmatch 'clock_timestamp|now\(\) 不用|不使用 now' }
foreach ($h in $nowHits) { Log "now-hit: $($h.Path):$($h.LineNumber)" }
$todoHits = Get-ChildItem "$bk\src" -Recurse -Filter *.py | Select-String -Pattern 'TODO|NotImplementedError'
foreach ($h in $todoHits) { Log "todo-hit: $($h.Path):$($h.LineNumber)" }
if ($nowHits.Count -gt 0 -or $todoHits.Count -gt 0) {
    Fail "scan" "grep now()/TODO in src" "none" "$($nowHits.Count) now-hits, $($todoHits.Count) todo-hits" "Fix real issues."
}
Log "scan clean: no now() lease logic, no TODO/NotImplemented in src"
Mark "Scan"

SaveState "COMPLETE" $script:done
Log "GATE=COMPLETE"
Log "PHASE1_PIPELINE_DONE"
exit 0
