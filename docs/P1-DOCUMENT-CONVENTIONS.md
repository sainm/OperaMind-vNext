# P1 Document Convention 与 Stable Key

## 当前实现

- `EmbeddingProfile`、`DocumentConventionProfile`、`CodeFrameworkProfile` 的版本化 JSON Schema 和 catalog。
- Profile 示例同时作为 baseline 可执行输入，不再根据文件名推断 Profile 类型。
- Document Convention 多 Variant 加权匹配。
- 低于阈值和最高分并列时进入 `needs_review`，不自动选择。
- Stable Key 只由有序 Canonical Business Field 构建，不依赖文件名、Sheet、列顺序或字段映射顺序。
- 可替换的 Office 提取器注册表，当前显式支持 `.xlsx` 和 `.docx`。
- 每个 Document Version 固定内容 SHA-256 与 extractor ref；ref 同时包含 OperaMind 适配器版本和实际 `openpyxl` / `python-docx` 版本。
- 解析前后重新计算来源文件摘要；文件在 Signal/Record 提取期间发生变化时整次 Diff 阻断，不保存混合 Snapshot。
- Office ZIP 在解析前执行文件大小、条目数、总展开量、加密成员和路径穿越检查。
- 从 Variant 表头别名提取带单元格/表格位置的 Observed Record，并映射为 Canonical Fact。
- XLSX 逐 Sheet 独立匹配 Variant；画面概要、画面项目、事件和 API 一览／详细表可以进入同一 Snapshot。只匹配文件名的无关 Sheet 会作为 `ignored_sections` 显式返回，命中表头但未达到唯一自动匹配条件的 Sheet 会阻断，不静默丢弃。
- Change Draft 与可执行 Diff 复用同一个 Canonical Snapshot Builder，不再分别执行整份工作簿与逐 Sheet 匹配。
- 按 Stable Key 对齐 before/after Canonical Snapshot，生成 Contract v1 `StructuredChange`。
- `operamind-diff` 将 Profile 匹配、Office 提取、Canonical 映射、Diff 和 Contract 校验串成可执行入口。
- migration `0002_p1_canonical_documents` 和 PostgreSQL Repository 持久化 Profile、项目激活审计、Document Snapshot、Fact 与 StructuredChange。
- migration `0003_structured_change_reviews` 以追加事件实现人工接受/拒绝，不修改原始 StructuredChange。
- migration `0004_structured_change_review_chain_guards` 在数据库层保证每个 Change 只有一个首事件、每个事件只有一个后继。
- migration `0056_snapshot_variant_provenance` 为 Snapshot 保存有序且完整的 Variant 集合，并以 `document_fact_variants` 固定每个 Fact 实际使用的 Variant；旧 Snapshot 按原 `selected_variant_id` 安全回填。
- `operamind-ingest` 在单一事务中写入 Profile 激活审计、before/after Snapshot、Fact、规范化 Change、Change Artifact 和 `DocumentIngestionResult v1`。Ingestion Artifact 固定初始状态事件 ID，以及每个 Document Profile 的数据库版本 ID、binding key、activation event ID 和语义引用；读取时必须与 Snapshot Membership 和激活审计完全一致。
- Profile 摘要校验不只发生在 `ProfileRepository.get_*`：Relation、Search Index 和 Context provenance 的直接 SQL 联表读取也会重算 payload SHA-256，并核对 profile type、ID 和 semantic version envelope，防止旁路读取接受漂移配置。
- Canonical Snapshot 读取要求每个 Fact 与同 Snapshot、同 Document Version 的 digest-validated Slice 一一对应，Stable Key、Fact Type、Summary、Canonical values 和 source refs 任一漂移都会阻断。正式 StructuredChange 读取还必须让规范化 Change/Fact 行与同 ID 的不可变 Artifact 完全一致；缺少 Artifact 不是可用的降级路径。

## Signal 语义

| Signal | 匹配规则 |
|---|---|
| `filename_token` | 任一配置值出现在规范化文件名中 |
| `sheet_name` | 任一配置值与当前 Sheet 名一致 |
| `heading` | 任一配置值与当前标题一致 |
| `headers` | 配置的全部表头均存在，顺序无关 |
| `business_term` | 任一配置业务词存在 |

匹配前统一执行 Unicode NFKC、首尾空白删除、连续空白合并和大小写折叠。每个 Variant 的 Signal 权重之和必须为 `1.0`。

## Office 信号提取边界

- XLSX：读取 Sheet 名；在每个 Sheet 的受限行列窗口中，单文本单元格行视作标题候选，多文本单元格行视作表头候选，全部文本作为业务词候选。横向键值可以位于任意列；纵向概要键值和由 Profile Alias 声明的分区标题可为后续表格提供 Stable Key 上下文。
- DOCX：读取 Heading/见出し样式段落、每个表格首行表头以及受限范围内的正文/表格业务词。
- 公式不作为结构信号，避免把可执行表达式误当作业务文本。
- `.xls`、`.doc`、`.xlsm` 等未注册格式直接失败，不做静默降级或扩展名猜测。
- before/after 必须是两个不同路径。输出 envelope 和 `DocumentIngestionResult` 同时记录两侧内容摘要与 extractor ref，PostgreSQL `document_versions` 也保存相同 provenance；升级前的历史记录明确标为 `legacy-unversioned@0`，不能冒充已固定解析器。

默认门禁为：压缩文件不超过 50 MiB、ZIP 条目不超过 10,000、总展开量不超过 200 MiB；XLSX 每个 Sheet 最多扫描 500 行 × 100 列，DOCX 最多扫描 2,000 个段落、每个表格 500 行 × 100 列。调用方可用 `ExtractionLimits` 收紧门禁。

## Canonical Fact 映射门禁

- 只有唯一 `auto_matched` 的 Variant 才允许进入字段映射；低置信度或并列结果继续阻断。
- 表头名称按照 NFKC、空白和大小写规则匹配 `field_aliases`。
- Profile 校验禁止同一个归一化别名映射到多个 Canonical Field。
- 同一 Canonical Field 若通过多个别名得到不同值，返回 `conflicting_field_values`，不选择任意一个值。
- Stable Key 字段缺失或为空时返回 `missing_stable_key_field`。
- 未映射源字段保留在结果的 `unmapped_fields` 中；已映射字段保留源别名和精确 source ref。
- 每个映射成功的 Fact 同时保留 `fact_ref -> variant_id`；Snapshot、Ingestion Artifact 和 PostgreSQL 三处回读必须完全一致。
- Canonical 业务值只执行空白归一化，保留原始大小写和全/半角标点；字段名匹配执行 NFKC，Stable Key 再按 Variant 的显式 normalizer 处理。来源位置不参与 Stable Key。

## StructuredChange 生成规则

- Snapshot 内 Stable Key 和 `fact_ref` 必须唯一，重复时直接阻断。
- Stable Key 同时存在且 Canonical `values` 相同：不生成变化，即使文件名、Sheet、列位置和 source ref 已改变。
- 只存在于 target：`added`；两侧值不同：`modified`；只存在于 source：`deleted`。
- `change_id` 由 project、source snapshot、target snapshot 和 Stable Key 确定性生成，同一次 Diff 可重复得到相同 ID。
- before/after Fact State 和合并后的 source refs 均进入 Artifact，输出再由 `StructuredChange v1` JSON Schema 校验。
- 默认 `confidence=high`、Artifact 内的 `review_status=needs_review`；人工审阅通过追加事件产生有效的 `accepted` 或 `rejected` 状态，Artifact 本身保持不可变。

Stable Key 的每个字段必须在 Variant 中显式声明 normalizer：

- `preserve`：保留大小写。
- `casefold`：用于不区分大小写的业务标识。
- `uppercase` / `lowercase`：用于有固定大小写规范的标识。

这样 `HTTP Method=GET` 可以保持大写，而 `画面ID=SCREEN_EXPENSE_LIST` 可以稳定生成 `screen_expense_list`，不在通用引擎中写领域特殊分支。

## 可执行 Diff

```bash
operamind-diff \
  --profile profiles/screen-design-convention-profile.example.json \
  --before /path/to/before.xlsx \
  --after /path/to/after.xlsx \
  --project-id visiondemo \
  --domain ui \
  --fact-type screen_element \
  --source-snapshot-id document-snapshot-before \
  --target-snapshot-id document-snapshot-after
```

默认向 stdout 输出包含 Fact 数量和 `StructuredChange v1` 数组的 JSON envelope；使用 `--output` 可写入已存在目录中的文件。任一文档需要 Variant 审阅、没有提取到记录、字段映射冲突或 Contract 校验失败时，命令返回非零。

需要持久化时，先运行 migration，并通过环境变量提供连接。命令要求调用方显式提供不可变文档引用、Profile 激活审计信息和所有版本 ID：

```bash
export OPERAMIND_DATABASE_URL='postgresql://...'
operamind-ingest \
  --profile profiles/screen-design-convention-profile.example.json \
  --before /path/to/before.xlsx \
  --after /path/to/after.xlsx \
  --project-id visiondemo \
  --analysis-case-id case-001 \
  --domain ui \
  --fact-type screen_element \
  --source-snapshot-id snapshot-before \
  --target-snapshot-id snapshot-after \
  --ingestion-batch-id ingestion-001 \
  --document-id screen-expense-list \
  --logical-name 02_画面設計書_経費精算申請一覧.xlsx \
  --source-document-version-id document-before \
  --target-document-version-id document-after \
  --source-ref immutable://design-docs/before.xlsx \
  --target-ref immutable://design-docs/after.xlsx \
  --profile-version-id screen-design-conventions-example@1.0.0 \
  --profile-binding-key document:screen_design \
  --profile-activation-event-id activation-001 \
  --activated-by reviewer@example.com \
  --activation-reason 'Reviewed convention for this project'
```

P1 导入／Diff 命令本身不建立 Embedding Index，因此它生成的首个结果如实使用 `embedding_index_status=not_started`、`indexed_target_count=0` 和 `status=needs_review`，不会伪装成 `ready_for_impact`。后续 P2 命令已经实现索引、检索和 readiness 推进。

## StructuredChange 人工审阅

首次接受或拒绝不提供上一事件 ID：

```bash
operamind-review-change \
  --project-id visiondemo \
  --change-id change-001 \
  --review-event-id review-001 \
  --decision accepted \
  --reviewed-by reviewer@example.com \
  --reason 'Compared with the source design'
```

需要反转已有决策时，必须显式携带当前事件 ID：

```bash
operamind-review-change \
  --project-id visiondemo \
  --change-id change-001 \
  --review-event-id review-002 \
  --decision rejected \
  --reviewed-by lead-reviewer@example.com \
  --reason 'Source evidence was re-evaluated' \
  --expected-previous-review-event-id review-001
```

Repository 对 Change 行加锁并检查预期上一事件。两个审核者从同一旧页面提交时，只有第一个决策能追加；第二个返回 stale review 错误。完全相同的 event ID 重放是 no-op，同一 ID 的不同内容返回 `PersistenceConflictError`。事件的复合外键还保证 previous event 属于同 Project、同 Change 且 previous status 与上一决策一致。

## P1 PostgreSQL 持久化

`0002_p1_canonical_documents.sql` 增加：

- 不可变 `profile_versions`、当前 `project_profile_bindings` 和追加式 `profile_activation_events`。
- `documents`、`document_versions`、`document_snapshots`、`snapshot_memberships`、`document_facts`。
- 规范化 `structured_changes`，before/after 通过复合外键绑定同 Project 的 source/target Snapshot、Fact、Stable Key 和 Fact Type。

`0003_structured_change_reviews.sql` 增加 `structured_change_review_events`，`0004_structured_change_review_chain_guards.sql` 用部分唯一索引阻止事件链分叉。原始 Change 行和 Contract Artifact 不更新；Repository 使用最新 review sequence 计算有效状态，并可按 Snapshot Pair 返回全部有效状态供后续 RAG 门禁使用。

Repository 写入使用确定性 ID 作为幂等键。相同内容重放成功；相同 ID 或唯一业务键对应不同内容时返回 `PersistenceConflictError`。Profile 和 Artifact 写入前校验 Schema；Profile 按版本或当前 Binding 回读时还会重算规范化摘要并比对类型、业务 ID 和语义版本，不能只凭 Schema 合法就接受漂移内容。Snapshot Fact 保留 values、source refs 和 field evidence。Artifact Repository 还会验证 Analysis Case 属于同一 Project，避免仅靠两个独立外键产生跨项目引用。

`PersistedDocumentDiffService` 先在数据库事务外完成安全提取与 Diff，再在一个外层事务中写入全部持久化结果；各 Repository 的内层事务成为 savepoint。任何末端 Artifact 校验、范围或冲突错误都会回滚此前的 Profile、Snapshot、Fact 和 Change 写入。

数据库集成测试需要独立数据库：

```bash
OPERAMIND_TEST_DATABASE_URL="$OPERAMIND_TEST_DATABASE_URL" \
  .venv/bin/pytest \
    tests/integration/test_migration_runner.py \
    tests/integration/test_artifact_repository.py \
    tests/integration/test_p1_canonical_repository.py \
    tests/integration/test_persisted_document_diff.py
```

## 自动匹配门禁

只有同时满足以下条件才返回 `auto_matched`：

1. 最高分达到 `minimum_auto_match_score`。
2. 只有一个 Variant 获得最高分。

其他情况保留全部候选和得分，并返回 `needs_review`。自动匹配本身不会写正式 Snapshot；持久化 Use Case 要求调用方显式提供 Profile Activation Event、操作者和原因，将激活审计与 committed Snapshot 放入同一事务。

## 真实样本验证

对 Golden source manifest 指向的真实文件做了以下只读验证：

- `ui-demo-after` 的 27/27 个 XLSX、合计 108 个 Sheet 均成功提取。
- 6 份 API 详细设计书的 API 一览与请求／响应明细 Sheet 同时匹配 `api-list-url`、`api-object-table`，before/after 各 81 条 Fact，逐 Fact Variant Provenance 完整且 Stable Key 唯一。
- API before/after Diff 为 0，符合本案例没有修改 API 设计书的事实。
- 画面设计书同时提取画面概要、画面项目、事件共 16 条 `screen_element` Fact（1+10+5），画面布局作为明确的忽略 Section 返回，只生成 1 条 `modified`。
- 该变化的 Stable Key 为 `screen_element:screen_expense_list/expense-search-status`，变化字段恰好为 `default_value` 和 `description`，source refs、confidence、review status 均匹配 silver 期待值。
- 在隔离 PostgreSQL 18 上真实执行 0001-0004，并通过 migration、Artifact、Profile、Canonical、StructuredChange、审阅事件链、stale writer、完整事务重放及失败回滚集成测试。
- 当前真实样本没有 DOCX，DOCX 路径由生成式单元测试覆盖。

## Stable Key

Stable Key 格式：

```text
<normalized fact type>:<encoded business value 1>/<encoded business value 2>
```

缺少或为空的 Stable Key Field 必须阻断，不允许退回行号、列号或内部节点 ID。

## 当前后续边界

- P2 的追加式 RAG readiness、三类 Query Plan、Profile 驱动 Relation Build、质量 evaluator、确定性 Context Package、真实 Provider test 与 readiness Evidence 已完成；模型压缩保真属于 MVP 后增强，不是当前 readiness 条件。
- Golden 案例已经冻结。正式 Golden retrieval 命令、质量报告和 Impact 门禁已实现并通过真实 PostgreSQL/pgvector 回归；VisionDemo 运维数据库上的正式 Report 仍需在本地 Embedding Provider 与固定 Canonical Snapshot 可用时采集，详见 [后续任务清单](NEXT-TASKS.md)。
