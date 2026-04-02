#!/usr/bin/env bash
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "========================================="
echo "  AStock 停止所有服务"
echo "========================================="

# ── Kill application processes ────────────────────────────────────────────────
if [ -f "$ROOT_DIR/.pids" ]; then
  source "$ROOT_DIR/.pids"
  for name_pid in "FastAPI:$API_PID" "Celery-Worker:$WORKER_PID" "Celery-Beat:$BEAT_PID" "Frontend:$FRONTEND_PID"; do
    name="${name_pid%%:*}"
    pid="${name_pid##*:}"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null && echo "  已停止 $name (PID $pid)" || true
    else
      echo "  $name 未运行 (PID $pid)"
    fi
  done
  rm -f "$ROOT_DIR/.pids"
else
  echo "  未找到 PID 文件，尝试按进程名停止..."
  pkill -f "uvicorn app.main:app" 2>/dev/null && echo "  已停止 FastAPI" || true
  pkill -f "celery.*astock" 2>/dev/null && echo "  已停止 Celery" || true
  pkill -f "vite.*5174" 2>/dev/null && echo "  已停止前端" || true
fi

# ── Stop Docker containers ────────────────────────────────────────────────────
echo ""
echo "停止 Docker 容器..."
cd "$ROOT_DIR"
docker-compose down
echo "  Docker 容器已停止"

echo ""
echo "========================================="
echo "  所有服务已停止"
echo "========================================="
