# P0 契约和数据基线

## 当前交付

- Python 3.12 项目和严格 Ruff/Mypy/Pytest 配置。
- 八个核心 Artifact 的版本化 catalog 和 Draft 2020-12 Schema 校验。
- 八个核心 Artifact 的 v1 可执行示例和 format 校验。
- Golden Dataset manifest、引用文件、案例身份和 MVP readiness 校验。
- PostgreSQL 初始 migration 和带 checksum 的不可变 migration runner。
- Artifact 写入前校验、不可变 JSONB 保存、SHA-256 摘要和回读再校验。

## 本地准备

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## 基线校验

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest
.venv/bin/operamind-baseline
```

`operamind-baseline` 验证当前 silver 数据结构。以下命令额外执行 MVP 开工门禁；当前预期失败，直至 Dataset 完成人工确认并冻结：

```bash
.venv/bin/operamind-baseline --require-ready
```

## PostgreSQL migration

数据库凭据只从环境变量读取。在空数据库执行：

```bash
OPERAMIND_DATABASE_URL="$OPERAMIND_DATABASE_URL" \
  .venv/bin/operamind-migrate
```

Runner 在一个事务内取得 PostgreSQL advisory lock，按版本顺序执行 migration，并保存源文件 SHA-256。再次执行输出 `Database schema is up to date`；已应用文件被重写时必须失败。

使用独立测试数据库运行 Repository round-trip：

```bash
OPERAMIND_TEST_DATABASE_URL="$OPERAMIND_TEST_DATABASE_URL" \
  .venv/bin/pytest tests/integration/test_artifact_repository.py
```

## 手动期待值

- 普通 baseline 输出 `OperaMind baseline validation passed`。
- readiness 校验必须阻断 silver、未冻结、少于两个项目、少于十二个案例、本地路径和未完成人工审阅。
- migration 在空 PostgreSQL 数据库完整提交，`schema_migrations` 记录 version `0001` 和 name `p0_baseline`。
- migration 重复运行不产生变化，已应用 SQL 的 checksum 改变会被拒绝。
- Repository round-trip 返回与写入前完全相同、且再次通过 Contract 的 Artifact。

## 已知限制

- 当前 Golden Dataset 仍是一个项目、一个案例的 silver 数据，不能宣称 MVP ready。
- migration 只建立 P0 身份、Revision、Analysis Case 和 Artifact exchange 基线；P1-P5 将按领域增加规范化 Canonical 表。
- 尚未提供 FastAPI 服务、Web、Embedding、Code Graph 提取、MCP 或 Playwright 运行能力。
