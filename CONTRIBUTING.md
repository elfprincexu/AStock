# 贡献指南

感谢你对 AStock 项目的关注！我们欢迎任何形式的贡献，包括但不限于：

- 报告 Bug
- 提出新功能建议
- 提交代码修复或新功能
- 改进文档
- 分享使用经验

## 开发环境搭建

### 前置条件

| 软件 | 版本要求 |
|------|----------|
| Python | 3.12+ |
| Node.js | 18+ |
| Docker & Docker Compose | Docker 20+, Compose v2+ |

### 本地开发

```bash
# 1. Fork 并克隆项目
git clone https://github.com/YOUR_USERNAME/AStock.git
cd AStock

# 2. 启动基础设施 (PostgreSQL + Redis + Grafana)
docker compose up -d

# 3. 安装后端依赖
pip install -r backend/requirements.txt
pip install -r tests/requirements.txt

# 4. 安装前端依赖
cd frontend && npm install && cd ..

# 5. 启动后端 (开发模式, 自动重载)
cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 6. 启动前端 (开发模式)
cd frontend && npm run dev
```

### 运行测试

```bash
# 全部测试
python -m pytest tests/ -v

# 指定测试文件
python -m pytest tests/test_aggregator.py -v

# 带覆盖率
python -m pytest tests/ -v --cov=backend/app
```

## 提交 Pull Request

### 流程

1. **Fork** 本仓库
2. 创建你的功能分支: `git checkout -b feature/my-feature`
3. 提交你的修改: `git commit -m "feat: 添加某某功能"`
4. 推送到你的分支: `git push origin feature/my-feature`
5. 打开一个 **Pull Request**

### 分支命名规范

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feature/` | 新功能 | `feature/add-xueqiu-source` |
| `fix/` | Bug 修复 | `fix/kline-gap-calculation` |
| `docs/` | 文档改进 | `docs/update-api-reference` |
| `refactor/` | 代码重构 | `refactor/aggregator-retry` |

### Commit Message 规范

建议遵循 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
<type>(<scope>): <description>

[optional body]
```

常用类型：

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档变更 |
| `style` | 代码格式 (不影响功能) |
| `refactor` | 代码重构 |
| `test` | 测试相关 |
| `chore` | 构建/工具变更 |

示例：
```
feat(quant): 添加 Alpha191 因子库
fix(aggregator): 修复北交所股票 K 线获取失败
docs: 更新数据源能力矩阵
```

## 代码规范

### Python (后端)

- 遵循 PEP 8 代码风格
- 类型注解：函数参数和返回值建议添加类型注解
- 异步优先：FastAPI 路由尽量使用 `async def`
- 错误处理：数据源客户端统一抛出 `DataSourceError`
- 新增数据源：继承 `DataSourceClient` 抽象基类，实现标准接口

### JavaScript/Vue (前端)

- 使用 Vue 3 Composition API
- 组件样式使用 Element Plus 组件库
- API 调用统一通过 `src/api/index.js`
- 遵循 A 股颜色惯例：红涨绿跌

### 数据库

- 新增表需要在 `models.py` 中定义 SQLAlchemy 模型
- 首次启动时自动建表，无需手动迁移

## 新增数据源指南

如果你想为 AStock 添加新的数据源，请遵循以下步骤：

1. 在 `backend/app/services/` 下创建新的客户端文件（如 `xueqiu.py`）
2. 继承 `DataSourceClient` 抽象基类
3. 实现 `get_realtime_quote()` 和/或 `get_daily_klines()` 方法
4. 在 `aggregator.py` 的客户端映射中注册新数据源
5. 在 `backend/app/services/__init__.py` 中导出
6. 编写对应的测试文件（参考 `tests/test_sina_api.py`）
7. 更新 `AGENTS.md` 和 `README.md` 中的数据源文档

## 报告 Bug

请通过 [GitHub Issues](../../issues/new?template=bug_report.md) 提交 Bug 报告。提交时请包含：

- 操作系统和 Python/Node.js 版本
- 复现步骤
- 期望行为 vs 实际行为
- 相关日志输出

## 功能建议

请通过 [GitHub Issues](../../issues/new?template=feature_request.md) 提交功能建议。

## 行为准则

请保持友善和尊重。我们致力于为所有参与者提供一个开放、包容的社区环境。

## 许可证

通过提交 Pull Request，你同意你的代码将按照本项目的 [MIT 许可证](LICENSE) 进行授权。
