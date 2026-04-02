#!/usr/bin/env bash
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "========================================="
echo "  AStock 一键启动脚本"
echo "========================================="

# ── Helper: ensure pyenv Python 3.12 is active ──
init_python() {
  export PYENV_ROOT="$HOME/.pyenv"
  export PATH="$PYENV_ROOT/bin:$PATH"
  eval "$(pyenv init -)"
  # .python-version = 3.12.11, pyenv will pick it up automatically
  cd "$ROOT_DIR"
  echo "  Python $(python --version 2>&1)"
}

# ── 0. Check Python ──────────────────────────────────────────────────────────
init_python

# Check if backend deps are installed
if ! python -c "import fastapi" 2>/dev/null; then
  echo "  安装后端依赖..."
  pip install -r "$ROOT_DIR/backend/requirements.txt" -q
fi

# ── 1. Start infrastructure ──────────────────────────────────────────────────
echo ""
echo "[1/4] 启动基础设施 (PostgreSQL, Redis, Grafana)..."
cd "$ROOT_DIR"
docker-compose up -d
echo "等待服务就绪..."
sleep 3

until docker exec astock-postgres pg_isready -U astock > /dev/null 2>&1; do
  echo "  等待 PostgreSQL..."
  sleep 2
done
echo "  PostgreSQL  OK  (localhost:5432)"

until docker exec astock-redis redis-cli ping > /dev/null 2>&1; do
  echo "  等待 Redis..."
  sleep 2
done
echo "  Redis       OK  (localhost:6379)"
echo "  Grafana     OK  (http://localhost:3000  admin/admin)"

# ── 2. Start backend API ─────────────────────────────────────────────────────
echo ""
echo "[2/4] 启动后端 API 服务..."
cd "$ROOT_DIR/backend"

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload > /tmp/astock-api.log 2>&1 &
API_PID=$!
echo "  FastAPI     OK  (PID $API_PID, http://localhost:8000)"
echo "  Swagger UI      http://localhost:8000/docs"

# ── 3. Start Celery ──────────────────────────────────────────────────────────
echo ""
echo "[3/4] 启动 Celery Worker + Beat..."
celery -A celery_app.celery worker --loglevel=info > /tmp/astock-celery-worker.log 2>&1 &
WORKER_PID=$!
echo "  Worker      OK  (PID $WORKER_PID)"

celery -A celery_app.celery beat --loglevel=info > /tmp/astock-celery-beat.log 2>&1 &
BEAT_PID=$!
echo "  Beat        OK  (PID $BEAT_PID)"

# ── 4. Start frontend ────────────────────────────────────────────────────────
echo ""
echo "[4/4] 启动前端开发服务器..."
cd "$ROOT_DIR/frontend"
if [ ! -d "node_modules" ]; then
  echo "  安装前端依赖..."
  npm install --silent
fi
npx vite --host 0.0.0.0 > /tmp/astock-frontend.log 2>&1 &
FRONTEND_PID=$!
echo "  Frontend    OK  (PID $FRONTEND_PID, http://localhost:5174)"

# ── Save PIDs ─────────────────────────────────────────────────────────────────
cat > "$ROOT_DIR/.pids" << PIDEOF
API_PID=$API_PID
WORKER_PID=$WORKER_PID
BEAT_PID=$BEAT_PID
FRONTEND_PID=$FRONTEND_PID
PIDEOF

echo ""
echo "========================================="
echo "  所有服务已启动"
echo "========================================="
echo ""
echo "  前端管理界面 :  http://localhost:5174"
echo "  后端 API     :  http://localhost:8000"
echo "  API 文档     :  http://localhost:8000/docs"
echo "  Grafana 看板 :  http://localhost:3000  (admin / admin)"
echo ""
echo "  日志文件:"
echo "    API    : /tmp/astock-api.log"
echo "    Worker : /tmp/astock-celery-worker.log"
echo "    Beat   : /tmp/astock-celery-beat.log"
echo "    前端   : /tmp/astock-frontend.log"
echo ""
echo "  停止: bash stop.sh"
echo "========================================="
