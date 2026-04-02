# AStock 部署指南

## 目录

- [环境要求](#环境要求)
- [快速部署（推荐）](#快速部署推荐)
- [配置说明](#配置说明)
- [数据初始化](#数据初始化)
- [常用运维命令](#常用运维命令)
- [更新升级](#更新升级)
- [故障排查](#故障排查)

---

## 环境要求

| 组件 | 最低版本 | 说明 |
|------|----------|------|
| Docker | 20.10+ | 容器运行时 |
| Docker Compose | 2.0+ | 服务编排（Docker Desktop 自带） |
| 磁盘空间 | 10 GB+ | 镜像约 3GB，数据库按股票数增长 |
| 内存 | 4 GB+ | 量化分析模块需要较多内存 |

> 不需要单独安装 Python、Node.js、PostgreSQL 或 Redis，全部在 Docker 容器中运行。

---

## 快速部署（推荐）

```bash
# 1. 克隆/解压项目
cd /path/to/AStock

# 2. 创建配置文件
cp .env.example .env
# 编辑 .env，按需修改数据库密码、LLM 配置等（见「配置说明」）

# 3. 一键启动全部服务
docker compose -f docker-compose.prod.yml up -d --build

# 4. 检查服务状态
docker compose -f docker-compose.prod.yml ps
```

启动完成后访问：

| 服务 | 地址 | 默认账号 |
|------|------|----------|
| **前端界面** | http://YOUR_IP:80 | AStock / AStock123! |
| **后端 API** | http://YOUR_IP:8000/docs | — |
| **Grafana** | http://YOUR_IP:3000 | admin / admin |

> 首次启动会自动建表并创建默认管理员账号 `AStock / AStock123!`，请登录后尽快修改密码。

---

## 配置说明

编辑项目根目录的 `.env` 文件：

### 必须配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `POSTGRES_PASSWORD` | 数据库密码 | `astock123` |
| `JWT_SECRET_KEY` | JWT 签名密钥（**生产环境务必修改**） | 内置默认值 |

### AI 分析（可选）

配置后可使用「AI 智能诊断」功能：

```env
# 示例：使用 DeepSeek
LITELLM_MODEL=deepseek/deepseek-chat
OPENAI_API_KEY=your-deepseek-api-key

# 示例：使用 OpenAI 兼容接口
LITELLM_MODEL=openai/your-model-name
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://your-endpoint/v1/
```

### 端口映射

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `FRONTEND_PORT` | 前端端口 | `80` |
| `BACKEND_PORT` | 后端 API 端口 | `8000` |
| `GRAFANA_PORT` | Grafana 端口 | `3000` |
| `POSTGRES_PORT` | PostgreSQL 端口（外部访问） | `5432` |

---

## 数据初始化

首次部署后，数据库为空。有两种方式获取股票数据：

### 方式一：通过 Web 界面（推荐）

1. 登录前端 → 搜索并添加感兴趣的股票到自选列表
2. 点击「抓取」按钮逐只获取数据
3. 或点击「全部抓取」批量获取

### 方式二：全量下载脚本（~5700 只 A 股）

```bash
# 进入 backend 容器
docker compose -f docker-compose.prod.yml exec backend bash

# 下载全部 A 股数据（K 线 + 基本面 + 行业，预计 2-4 小时）
python /app/scripts/download_all_data.py

# 仅下载 K 线
python /app/scripts/download_all_data.py --klines-only

# 仅下载基本面 (PE/PB/市值)
python /app/scripts/download_all_data.py --fundamentals-only
```

> **注意：** scripts 和 data 目录已包含在 backend 镜像中。

---

## 常用运维命令

```bash
# 查看所有服务状态
docker compose -f docker-compose.prod.yml ps

# 查看日志
docker compose -f docker-compose.prod.yml logs -f backend          # 后端 API
docker compose -f docker-compose.prod.yml logs -f celery-worker    # Celery 任务
docker compose -f docker-compose.prod.yml logs -f celery-beat      # 定时调度
docker compose -f docker-compose.prod.yml logs -f frontend         # 前端 Nginx

# 重启单个服务
docker compose -f docker-compose.prod.yml restart backend

# 停止所有服务
docker compose -f docker-compose.prod.yml down

# 停止并删除数据（谨慎！）
docker compose -f docker-compose.prod.yml down -v

# 进入后端容器执行命令
docker compose -f docker-compose.prod.yml exec backend bash

# 数据库备份
docker compose -f docker-compose.prod.yml exec postgres \
  pg_dump -U astock astock > backup_$(date +%Y%m%d).sql

# 数据库恢复
docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U astock astock < backup_20260327.sql
```

---

## 更新升级

```bash
# 1. 拉取/更新代码
cd /path/to/AStock

# 2. 重新构建并启动（--build 重建镜像）
docker compose -f docker-compose.prod.yml up -d --build

# 数据库数据保存在 Docker volume 中，不会丢失
```

---

## 故障排查

### 后端启动失败

```bash
# 查看详细日志
docker compose -f docker-compose.prod.yml logs backend

# 常见原因：
# 1. 数据库未就绪 → 等待 postgres healthcheck 通过后重试
# 2. .env 配置错误 → 检查数据库连接信息
```

### 前端无法访问 API

```bash
# 检查 backend 是否正常运行
docker compose -f docker-compose.prod.yml ps backend
curl http://localhost:8000/api/health

# Nginx 日志
docker compose -f docker-compose.prod.yml logs frontend
```

### Celery 任务不执行

```bash
# 检查 worker 和 beat 状态
docker compose -f docker-compose.prod.yml ps celery-worker celery-beat
docker compose -f docker-compose.prod.yml logs celery-worker
docker compose -f docker-compose.prod.yml logs celery-beat

# 检查 Redis 连接
docker compose -f docker-compose.prod.yml exec redis redis-cli ping
```

### 端口冲突

如果默认端口（80、8000、5432 等）被占用，在 `.env` 中修改对应的 `*_PORT` 变量。

---

## 架构说明

```
┌─────────────────────────────────────────────────────┐
│                   Docker Network                     │
│                                                      │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐       │
│  │ Frontend │    │ Backend  │    │  Celery  │       │
│  │  (Nginx) │───▶│ (FastAPI)│    │  Worker  │       │
│  │  :80     │/api│  :8000   │    │          │       │
│  └──────────┘    └────┬─────┘    └────┬─────┘       │
│                       │               │              │
│                  ┌────▼───┐      ┌────▼───┐         │
│                  │PostgreSQL│     │  Redis │         │
│                  │  :5432  │     │  :6379 │         │
│                  └─────────┘     └────────┘         │
│                       │                              │
│                  ┌────▼───┐    ┌──────────┐         │
│                  │Grafana │    │  Celery  │         │
│                  │ :3000  │    │   Beat   │         │
│                  └────────┘    └──────────┘         │
└─────────────────────────────────────────────────────┘
```

| 容器 | 镜像 | 说明 |
|------|------|------|
| `astock-frontend` | 自建 (Nginx + Vue) | 前端 SPA + API 反向代理 |
| `astock-backend` | 自建 (Python 3.12) | FastAPI REST API |
| `astock-celery-worker` | 同 backend | 异步任务执行 |
| `astock-celery-beat` | 同 backend | 定时任务调度 |
| `astock-postgres` | postgres:16-alpine | 数据库 |
| `astock-redis` | redis:7-alpine | 消息队列 & 缓存 |
| `astock-grafana` | grafana:11.1.0 | 可视化监控 |
