# P0 契约和数据基线

## 当前交付

- Python 3.12 项目和严格 Ruff/Mypy/Pytest 配置。
- 26 個のコア Artifact のバージョン化 catalog と Draft 2020-12 Schema 検証。
- 26 個のコア Artifact の実行可能 Example と format 検証。
- Golden Dataset manifest、引用文件、案例身份和 MVP readiness 校验。
- Golden RAG expectation Schema、三类 Query 冻结 ID/阈值 readiness 校验，以及离线 Recall@5/10、MRR、无关率和跨项目泄漏 evaluator。
- Golden UI expectation Schema、Project/Case 绑定、Scenario ID/基线结果完整性和业务/QA 批准 readiness 门禁。
- PostgreSQL 初始 migration 和带 checksum 的不可变 migration runner。
- Artifact 写入前校验、不可变 JSONB 保存、SHA-256 摘要和回读再校验。

## 本地准备

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
```

## 基线校验

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest
.venv/bin/operamind-baseline
```

`operamind-baseline` 默认验证 silver 开发数据。默认 silver manifest 使用 `--require-ready` 会按预期失败；验证当前冻结 Golden Dataset 和 repository-wide readiness 时必须显式选择对应 manifest：

```bash
.venv/bin/operamind-baseline \
  --manifest golden-dataset/manifest.golden.json \
  --readiness-manifest readiness/mvp-readiness.json \
  --require-ready
.venv/bin/operamind-baseline \
  --manifest golden-dataset/manifest.golden.json \
  --readiness-manifest readiness/mvp-readiness.json \
  --require-mvp-ready
```

`--require-ready` 只检查冻结 Golden Dataset 的离线回归质量，不是个别 Change Request 的运行时 Impact 门禁。`--require-mvp-ready` 还要求 `readiness/mvp-readiness.json` 中真实 Provider、范围授权、GitHub Copilot、绑定 target Deployment E2E 和完整 PostgreSQL/Edge 回归全部具有经审核、SHA-256 固定且符合 `readiness/mvp-evidence.schema.json` 的类型化证据。Golden 证据必须与本次命令选择并校验的 manifest 完全一致；完整回归证据的 `operamind-source-tree-v1` 摘要会由校验器重新计算。当前 source tree 必须在具备 PostgreSQL 和 Edge 的验证环境中重建全回归 Evidence，且 `github_copilot_live` 仍为 pending，因此第二个命令按预期失败；不能用代码存在、空证据摘要或 Fake 测试替代真实证据。

## PostgreSQL migration

数据库凭据只从环境变量读取。在空数据库执行：

```bash
OPERAMIND_DATABASE_URL="$OPERAMIND_DATABASE_URL" \
  .venv/bin/operamind-migrate
```

Runner 在一个事务内取得 PostgreSQL advisory lock，按版本顺序执行 migration，并保存源文件 SHA-256。再次执行输出 `Database schema is up to date`；已应用文件被重写时必须失败。

使用具有 `CREATEDB` 权限的测试管理连接运行 Repository round-trip。Pytest 会自动创建、迁移并删除随机数据库，不会修改 URL 指向的原数据库：

```bash
OPERAMIND_TEST_DATABASE_URL="$OPERAMIND_TEST_DATABASE_URL" \
  .venv/bin/pytest tests/integration/test_artifact_repository.py
```

## 手动期待值

- 普通 baseline 输出 `OperaMind baseline validation passed`。
- readiness 校验必须阻断 silver、AI 候选、未冻结、零案例、超过五个案例、本地路径和未完成人工审阅。
- migration 在空 PostgreSQL 数据库完整提交；当前 catalog 顺序记录 `0001` 至 `0051/web_command_idempotency`。
- migration 重复运行不产生变化，已应用 SQL 的 checksum 改变会被拒绝。
- Repository round-trip 返回与写入前完全相同、且再次通过 Contract 的 Artifact。

## 已知限制

- 当前已冻结一个由 VisionDemo Silver 检查依据支撑的 Golden 案例，并记录对话审核身份，因此可以宣称 Golden ready。业务负责人／开发／QA 的分角色审核仍可作为治理增强；MVP ready 只因 `github_copilot_live` 外部 gate 尚未通过而不能宣称。
- Repository-wide MVP readiness manifest 对缺失或漂移证据保持 pending；`operamind-readiness` 从 Canonical PostgreSQL 和真实测试 observation 幂等生成证据、摘要并原子更新 manifest，只有所有真实/本地证据均存在、digest 匹配并经审核后，`--require-mvp-ready` 才可能通过。
- `0001` 建立 P0 身份、Revision、Analysis Case 和 Artifact exchange 基线；`0002-0004` 增加 P1 Canonical Document/Profile/Change/Review；`0005-0007` 增加 P2 Canonical Node、pgvector Search Index、RAG 状态事件和 Relation Build；`0008-0011` 增加 P3/P4 Code Graph、Impact、Edit Packet 和 Edit Result；`0012-0028` 固定 UI 验证、Browser Manifest、UI Knowledge、Approval Grant、安全命令、Evidence 完整性、历史隔离、Repository Binding 与恢复摘要；`0029-0041` 增加 Readiness、日文 Web、Change Orchestration、Test Data／Closure 与历史修订能力；`0042-0054` 增加增量 Graph、Bridge 恢复、内部 Task／Worker 与 Profile drift 台账；`0055` 增加 Golden RAG 离线质量报告；`0056` 增加多 Sheet Snapshot 与逐 Fact Variant Provenance；`0057-0059` 增加统一 Copilot Change Task、阶段输出和同一 Task 的执行范围绑定生命周期；`0060` 将 Closure 的 UI 验证外键切换到当前不可变 Artifact 存储。所有 Artifact 复用 P0 不可变 Artifact 存储。
- 已提供 Embedding Adapter、pgvector Build、requirement/hybrid retrieval、Context Package、内部 Approval Grant、无 shell 安全命令执行器、统一 Change Task 的五个主 MCP Tool、loopback local Bridge、可安装 VSIX、TestData/UI Runner、Readiness Evidence 同步器和六工程日文 Web。真实本地 Nomic Provider 与既存 target deployment E2E 有历史证据；重构后的完整 GitHub Copilot 会话和最终 source tree E2E 仍需重新生成。
