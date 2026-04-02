#Requires -Version 5.1
<#
.SYNOPSIS
    AStock 一键启动脚本 (Windows PowerShell)
.DESCRIPTION
    依次启动 Docker 基础设施、FastAPI 后端、Celery Worker/Beat、Vue 前端。
    日志写入 $env:TEMP\astock-*.log，进程 ID 保存到 .pids.json。
.NOTES
    用法: .\start.ps1
    停止: .\stop.ps1
#>

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$LogDir  = $env:TEMP

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  AStock 一键启动脚本 (Windows)"           -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# ── 0. Check Python ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[0/4] 检查 Python 环境..."

# Try pyenv-win first, then fall back to system python
$pyenvRoot = $env:PYENV_ROOT
if (-not $pyenvRoot) { $pyenvRoot = "$env:USERPROFILE\.pyenv\pyenv-win" }
if (Test-Path "$pyenvRoot\bin\pyenv.bat") {
    $env:PATH = "$pyenvRoot\bin;$pyenvRoot\shims;$env:PATH"
}

try {
    $pyVer = & python --version 2>&1
    Write-Host "  $pyVer" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Python 未找到。请安装 Python 3.12+ (推荐 pyenv-win)" -ForegroundColor Red
    exit 1
}

# Check backend deps
$depsMissing = $false
try { & python -c "import fastapi" 2>$null } catch { $depsMissing = $true }
if ($LASTEXITCODE -ne 0) { $depsMissing = $true }
if ($depsMissing) {
    Write-Host "  安装后端依赖..."
    & pip install -r "$RootDir\backend\requirements.txt" -q
}

# ── 1. Start infrastructure ──────────────────────────────────────────────────
Write-Host ""
Write-Host "[1/4] 启动基础设施 (PostgreSQL, Redis, Grafana)..." -ForegroundColor Yellow
Set-Location $RootDir
& docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] Docker Compose 启动失败。请确认 Docker Desktop 已运行。" -ForegroundColor Red
    exit 1
}

Write-Host "  等待服务就绪..."
Start-Sleep -Seconds 3

# Wait for PostgreSQL
$pgReady = $false
for ($i = 0; $i -lt 30; $i++) {
    $null = & docker exec astock-postgres pg_isready -U astock 2>$null
    if ($LASTEXITCODE -eq 0) { $pgReady = $true; break }
    Write-Host "  等待 PostgreSQL..." -ForegroundColor Gray
    Start-Sleep -Seconds 2
}
if (-not $pgReady) {
    Write-Host "  [ERROR] PostgreSQL 未就绪，超时退出" -ForegroundColor Red
    exit 1
}
Write-Host "  PostgreSQL  OK  (localhost:5432)" -ForegroundColor Green

# Wait for Redis
$redisReady = $false
for ($i = 0; $i -lt 15; $i++) {
    $null = & docker exec astock-redis redis-cli ping 2>$null
    if ($LASTEXITCODE -eq 0) { $redisReady = $true; break }
    Write-Host "  等待 Redis..." -ForegroundColor Gray
    Start-Sleep -Seconds 2
}
if (-not $redisReady) {
    Write-Host "  [ERROR] Redis 未就绪，超时退出" -ForegroundColor Red
    exit 1
}
Write-Host "  Redis       OK  (localhost:6379)" -ForegroundColor Green
Write-Host "  Grafana     OK  (http://localhost:3000  admin/admin)" -ForegroundColor Green

# ── 2. Start backend API ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "[2/4] 启动后端 API 服务..." -ForegroundColor Yellow
$apiLog = "$LogDir\astock-api.log"
$apiProc = Start-Process -FilePath python -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload" `
    -WorkingDirectory "$RootDir\backend" -WindowStyle Hidden `
    -RedirectStandardOutput $apiLog -RedirectStandardError "$LogDir\astock-api-err.log" `
    -PassThru
Write-Host "  FastAPI     OK  (PID $($apiProc.Id), http://localhost:8000)" -ForegroundColor Green
Write-Host "  Swagger UI      http://localhost:8000/docs" -ForegroundColor Green

# ── 3. Start Celery ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[3/4] 启动 Celery Worker + Beat..." -ForegroundColor Yellow

$workerLog = "$LogDir\astock-celery-worker.log"
$workerProc = Start-Process -FilePath celery -ArgumentList "-A", "celery_app.celery", "worker", "--loglevel=info", "--pool=solo" `
    -WorkingDirectory "$RootDir\backend" -WindowStyle Hidden `
    -RedirectStandardOutput $workerLog -RedirectStandardError "$LogDir\astock-celery-worker-err.log" `
    -PassThru
Write-Host "  Worker      OK  (PID $($workerProc.Id), --pool=solo)" -ForegroundColor Green

$beatLog = "$LogDir\astock-celery-beat.log"
$beatProc = Start-Process -FilePath celery -ArgumentList "-A", "celery_app.celery", "beat", "--loglevel=info" `
    -WorkingDirectory "$RootDir\backend" -WindowStyle Hidden `
    -RedirectStandardOutput $beatLog -RedirectStandardError "$LogDir\astock-celery-beat-err.log" `
    -PassThru
Write-Host "  Beat        OK  (PID $($beatProc.Id))" -ForegroundColor Green

# ── 4. Start frontend ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[4/4] 启动前端开发服务器..." -ForegroundColor Yellow
Set-Location "$RootDir\frontend"
if (-not (Test-Path "node_modules")) {
    Write-Host "  安装前端依赖..."
    & npm install --silent
}

$frontendLog = "$LogDir\astock-frontend.log"
$frontendProc = Start-Process -FilePath npx -ArgumentList "vite", "--host", "0.0.0.0" `
    -WorkingDirectory "$RootDir\frontend" -WindowStyle Hidden `
    -RedirectStandardOutput $frontendLog -RedirectStandardError "$LogDir\astock-frontend-err.log" `
    -PassThru
Write-Host "  Frontend    OK  (PID $($frontendProc.Id), http://localhost:5174)" -ForegroundColor Green

# ── Save PIDs ────────────────────────────────────────────────────────────────
Set-Location $RootDir
$pids = @{
    API      = $apiProc.Id
    Worker   = $workerProc.Id
    Beat     = $beatProc.Id
    Frontend = $frontendProc.Id
}
$pids | ConvertTo-Json | Set-Content -Path "$RootDir\.pids.json" -Encoding UTF8

Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  所有服务已启动"                          -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  前端管理界面 :  http://localhost:5174"
Write-Host "  后端 API     :  http://localhost:8000"
Write-Host "  API 文档     :  http://localhost:8000/docs"
Write-Host "  Grafana 看板 :  http://localhost:3000  (admin / admin)"
Write-Host ""
Write-Host "  日志文件 ($LogDir):"
Write-Host "    API    : $apiLog"
Write-Host "    Worker : $workerLog"
Write-Host "    Beat   : $beatLog"
Write-Host "    前端   : $frontendLog"
Write-Host ""
Write-Host "  停止: .\stop.ps1" -ForegroundColor Yellow
Write-Host "=========================================" -ForegroundColor Cyan
