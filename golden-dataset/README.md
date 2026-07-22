# Golden Dataset

Golden Dataset 是业务、开发和测试共同确认的标准答案，不是为了配合当前实现而调整的普通 fixture。

每个案例必须包含：

```text
before documents
after documents
target repository and fixed commit
expected Structured Changes
expected RAG section/chunk IDs
expected must-include and must-exclude code paths
expected UI scenarios and visible results
```

Manifest 通过 `source_manifest` 统一引用 before/after 文档来源和固定代码 Revision。`silver` 可用于开发 fixture；AI 辅助生成的数据在确认前必须标记为 `golden_candidate / needs_review` 并记录 `candidate_provenance`。当前 `manifest.golden.json` 只选择了 1 个有既存 VisionDemo Silver 检查结果支撑的案例；用户已在当前 Codex 对话中确认需要人工判断的内容，案例已提升为 `golden / frozen`。正式证据 `readiness/evidence/golden-dataset-1.0.0.json` 同时固定 manifest SHA-256 和覆盖 manifest、Schema、全部引用 JSON 的 `operamind-golden-dataset-v1` 摘要。

正式 Golden manifest 记录至少一个可审计确认身份。每个案例的 `review.json` 将人工判断拆成 source identity、expected change/RAG、code scope、UI scenarios 四个步骤；同一确认人可以完成全部步骤。Schema、哈希、路径、ID、覆盖率等确定性条件由校验器自动判定，不要求重复人工审批。候选阶段禁止提前填写确认身份或批准结果。

RAG 期待值必须通过 `expected-rag-context.schema.json`。Golden 阶段需要为业务行为、精确锚点、验收标准三类 Query 分别冻结 required/irrelevant Canonical node ID，并明确 Recall@5/10、MRR、无关候选率和跨 Project 泄漏阈值。`observed-rag-results.schema.json` 定义实际排名输入，`operamind-evaluate-rag` 只计算和判定，不生成或修改期待值。

UI 期待值必须通过 `expected-ui-scenarios.schema.json`，绑定 `case_id` 与 `project_id`，每个 Scenario ID 唯一，并为每个 Scenario 固定当前基线结果。Golden readiness 只接受业务和 QA 已批准、UI impact 已明确的期待值；未批准的 silver 场景不能被当作 E2E 通过证据。

MVP 接受 1–5 个真实、可复核案例；当前冻结的 1 个案例覆盖：

- 页面状态筛选器默认值与可选项变化。
- 空状态查询的后端契约及多代码文件影响范围。
- RAG 业务语义、精确锚点和验收标准三类期待值。
- 三个可见 UI 验收场景。

双入口 Change Loop 另有 3 份通过 `change-loop-case.schema.json` 校验的可执行案例配置：既存的经费状态案例，以及新增的社員姓名空白检索、発注状态／仕入先检索案例。案例配置固定需求意图、文档差异、影响候选、精确代码替换、测试数据、验收条件、API 断言和受限 Browser Scenario；歧义、冲突、Revision 不一致或编辑越界仍会停止并要求人工确认。这两份新增配置用于多案例闭环回归，不会自动扩大 `manifest.golden.json` 中已冻结的正式 Golden 范围。

`operamind-change-cases` 用于案例初始化、自动发现、完整性校验和隔离批量运行。初始化只生成不可执行的 `draft`；校验同时检查配置 Schema、跨对象 ID、源清单、文档 SHA-256、Profile、固定 Git Revision 和引用代码路径。批量结果通过 `change-loop-batch-report.schema.json` 校验，并明确区分需要确认、需要重新分析、环境失败和业务失败。

真实设计书可以脱敏，但必须保留结构、关系和变化特征。代码仓库不复制到 Dataset，只保存 URL、commit、scan roots 和期待路径。

校验结构：

```bash
operamind-baseline --manifest golden-dataset/manifest.golden.json
```

校验冻结 Dataset 及其 readiness evidence：

```bash
operamind-baseline \
  --manifest golden-dataset/manifest.golden.json \
  --readiness-manifest readiness/mvp-readiness.json \
  --require-ready
```

当前上述 `--require-ready` 命令应通过。它只证明 Golden Dataset gate，不等于 MVP ready；完整本地回归已由机器验证，完整 MVP 仍需要真实 Provider、人工审批 E2E、GitHub Copilot live session 和真实 target deployment E2E evidence。

人工审核后，先把正式 envelope 放入 `readiness/evidence/` 并单独预检，不能直接修改 gate：

```bash
operamind-baseline \
  --manifest golden-dataset/manifest.golden.json \
  --validate-reviewed-evidence readiness/evidence/golden-dataset-1.0.0.json
```
