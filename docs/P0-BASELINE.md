# P0 契约和数据基线

## 当前交付

- Python 3.12 项目和严格 Ruff/Mypy/Pytest 配置。
- 二十二个核心 Artifact 的版本化 catalog 和 Draft 2020-12 Schema 校验。
- 二十二个核心 Artifact 的 v1 可执行示例和 format 校验。
- Golden Dataset manifest、引用文件、案例身份和 MVP readiness 校验。
- Golden RAG expectation Schema、三类 Query 冻结 ID/阈值 readiness 校验，以及离线 Recall@5/10、MRR、无关率和跨项目泄漏 evaluator。
- Golden UI expectation Schema、Project/Case 绑定、Scenario ID/基线结果完整性和业务/QA 批准 readiness 门禁。
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
.venv/bin/operamind-baseline --require-mvp-ready
```

`--require-ready` 只检查冻结 Golden Dataset；`--require-mvp-ready` 还要求 `readiness/mvp-readiness.silver.json` 中真实 Provider、人工审批、GitHub Copilot、绑定 target Deployment E2E 和完整 PostgreSQL/Chrome 回归全部具有经审核、SHA-256 固定且符合 `readiness/mvp-evidence.schema.json` 的类型化证据。Golden 证据必须与本次命令选择并校验的 manifest 完全一致；完整回归证据的 `operamind-source-tree-v1` 摘要会由校验器按源码、测试、migration、Contract/Profile 和项目配置重新计算，不能手填任意哈希。当前后者预期失败，不能把代码存在、空证据摘要或 Fake 测试当作外部通过证据。

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
- readiness 校验必须阻断 silver、AI 候选、未冻结、零案例、超过五个案例、本地路径和未完成人工审阅。
- migration 在空 PostgreSQL 数据库完整提交；当前 catalog 顺序记录 `0001` 至 `0044/copilot_bridge_recovery`。
- migration 重复运行不产生变化，已应用 SQL 的 checksum 改变会被拒绝。
- Repository round-trip 返回与写入前完全相同、且再次通过 Contract 的 Artifact。

## 已知限制

- 当前只选中了一个项目中有 Silver 检查依据的 AI 辅助候选案例；用户已选择批准，但尚未提供可审计审核身份，因此不能宣称 Golden ready 或 MVP ready。
- Repository-wide MVP readiness manifest 对缺失或漂移证据保持 pending；`operamind-readiness` 从 Canonical PostgreSQL 和真实测试 observation 幂等生成证据、摘要并原子更新 manifest，只有所有真实/本地证据均存在、digest 匹配并经审核后，`--require-mvp-ready` 才可能通过。
- `0001` 建立 P0 身份、Revision、Analysis Case 和 Artifact exchange 基线；`0002-0004` 增加 P1 Canonical Document/Profile/Change/Review；`0005-0006` 增加 P2 Canonical Node、pgvector cache、Search Index Build 和追加式 DocumentIngestionResult 状态事件；`0007` 增加版本化 Relation Build、unresolved 台账及 Search Index 的 Relation Build 绑定；`0008` 增加 P3 Code Graph Snapshot、File、Symbol、Edge 和 Test Binding；`0009` 增加 P4 Impact Report、Item 和追加式 Confirmation；`0010` 增加 active/superseded Edit Packet 白名单；`0011` 增加 path-only Edit Result 和范围门禁；`0012` 增加 P5 Scenario、Deployment、Plan、Preflight、Run、Evidence 和 Change Validation；`0013-0028` 逐步固定 Browser Manifest、Preflight、UI Knowledge、Locator Observation、Approval Grant、安全命令、Evidence 完整性、历史隔离、Repository Binding 与恢复摘要；`0029-0035` 增加 Readiness、日文 Web 控制面、Change Orchestration、TestDataPlan 执行、ChangeClosureResult、Web Run 控制与 UI Scenario/Test Case 映射；`0036` 增加自然语言 Test Case Version 修订与 stale 台账；`0037` 增加修订后执行范围摘要、Grant 复用／重新确认及不可变授权记录；`0038` 增加 UI Knowledge 观测截图 Evidence 及跨 Scope 完整性约束；`0039` 增加补偿性 Test Case Revision 撤销关系和唯一性约束；`0040` 增加可恢复的一键编排 Run 与不可变事件账本；`0041` 增加自然语言请求导入 Case 的一次性审计绑定；`0042` 增加 Code Graph Revision 增量扫描；`0043` 增加无文件 `CopilotCodingTask`、Bridge 队列、事件和命令／Edit Result 绑定；`0044` 增加 claim lease、超时接管、取消终态、retry lineage 和 attempt number。所有 Artifact 复用 P0 不可变 Artifact 存储。
- 已提供 Embedding Adapter、pgvector Build、hybrid retrieval、Context Package、显式 opt-in 的真实 Provider live 合约测试入口、Approval Grant、无 shell安全命令执行器、十三工具 Copilot stdio MCP Server、loopback local Bridge、可安装 VSIX、Playwright Runner、Readiness Evidence 同步器和日文 FastAPI/Web 控制面。真实本地 Nomic Provider 与 target deployment E2E 已有证据；真实 GitHub Copilot 完成会话仍是外部门禁。
