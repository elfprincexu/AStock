#Requires -Version 5.1
<#
.SYNOPSIS
    AStock 一键停止脚本 (Windows PowerShell)
.DESCRIPTION
    停止 start.ps1 启动的所有服务进程和 Docker 容器。
.NOTES
    用法: .\stop.ps1
#>

$ErrorActionPreference = "Continue"
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$PidFile = "$RootDir\.pids.json"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  AStock 停止所有服务"                     -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# ── Kill application processes ────────────────────────────────────────────────
if (Test-Path $PidFile) {
    $pids = Get-Content $PidFile -Raw | ConvertFrom-Json

    $services = @(
        @{ Name = "FastAPI";       PID = $pids.API }
        @{ Name = "Celery-Worker"; PID = $pids.Worker }
        @{ Name = "Celery-Beat";   PID = $pids.Beat }
        @{ Name = "Frontend";      PID = $pids.Frontend }
    )

    foreach ($svc in $services) {
        $pid = $svc.PID
        $name = $svc.Name
        if ($pid) {
            try {
                $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
                if ($proc -and -not $proc.HasExited) {
                    # Kill the process tree (parent + children)
                    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                    # Also kill child processes (uvicorn spawns workers, npm spawns node, etc.)
                    Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $pid } | ForEach-Object {
                        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                    }
                    Write-Host "  已停止 $name (PID $pid)" -ForegroundColor Green
                } else {
                    Write-Host "  $name 未运行 (PID $pid)" -ForegroundColor Gray
                }
            } catch {
                Write-Host "  $name 未运行 (PID $pid)" -ForegroundColor Gray
            }
        }
    }

    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "  未找到 PID 文件，尝试按进程名停止..." -ForegroundColor Yellow

    # Fallback: kill by process name patterns
    $patterns = @(
        @{ Name = "FastAPI"; Filter = "uvicorn" }
        @{ Name = "Celery";  Filter = "celery" }
        @{ Name = "Frontend (node)"; Filter = "node" }
    )

    foreach ($p in $patterns) {
        $procs = Get-Process | Where-Object {
            $_.ProcessName -like "*$($p.Filter)*" -or
            ($_.CommandLine -and $_.CommandLine -like "*$($p.Filter)*")
        }
        if ($procs) {
            $procs | Stop-Process -Force -ErrorAction SilentlyContinue
            Write-Host "  已停止 $($p.Name)" -ForegroundColor Green
        }
    }
}

# ── Stop Docker containers ────────────────────────────────────────────────────
Write-Host ""
Write-Host "停止 Docker 容器..." -ForegroundColor Yellow
Set-Location $RootDir
& docker compose down
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Docker 容器已停止" -ForegroundColor Green
} else {
    Write-Host "  Docker 容器停止失败或未运行" -ForegroundColor Gray
}

# ── Clean up log files (optional) ─────────────────────────────────────────────
$logFiles = @(
    "$env:TEMP\astock-api.log",
    "$env:TEMP\astock-api-err.log",
    "$env:TEMP\astock-celery-worker.log",
    "$env:TEMP\astock-celery-worker-err.log",
    "$env:TEMP\astock-celery-beat.log",
    "$env:TEMP\astock-celery-beat-err.log",
    "$env:TEMP\astock-frontend.log",
    "$env:TEMP\astock-frontend-err.log"
)
# Note: logs are NOT deleted automatically. Uncomment below to clean up:
# $logFiles | ForEach-Object { Remove-Item $_ -Force -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  所有服务已停止"                          -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
