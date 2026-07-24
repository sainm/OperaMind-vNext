# P2 真实 RAG 实现

## 当前完成范围

- `0005_rag_document_nodes_and_search_index` 安装 pgvector，并增加 Canonical `document_nodes`、`document_relations`、`search_index_builds`、`document_search_vectors` 和 `search_index_entries`；`0006_document_ingestion_result_events` 增加不可变的 DocumentIngestionResult 状态事件链和 Project 复合外键；`0007_document_relation_builds` 增加版本化 Relation Build、Build Entry、unresolved 台账以及 Search Index 的 Relation Build 外键；`0025_search_index_failure_audit` 为失败 Build 增加唯一事件 ID、分类、操作者和可选恢复截止时间；`0027_relation_build_plan_digest` 为完整 relation/unresolved 账本增加版本化 SHA-256；`0028_search_index_entry_ledger_digest` 固定每个索引条目的作用域、关键词、向量身份和实际 pgvector 二进制内容摘要；`0055_golden_rag_quality_gate` 保存精确绑定 Snapshot／Profile／Index 的 Golden 质量报告和三类 Query 结果。
- 文档导入事务会为每个 Fact Type 生成不可索引 Section，为每个 Canonical Fact 生成可索引 Slice。节点引用 Snapshot Membership，不以本地路径、行号或 source ref 作为节点身份或 embedding 内容。
- embedding 输入确定性包含 document type、heading path、business keys、section summary、slice content 和 relation labels，并将 `preprocessing_version` 纳入 input digest。Context rehydration 与 Search Index target 加载都会重算 Canonical DocumentNode 的语义 `content_digest`；合法文本被数据库内改写但摘要未同步时直接阻断。
- `DocumentRelationProfile` 用 source/target document type、fact type 和等长 Canonical 字段元组定义精确连接。`nfkc_casefold`、`preserve` 和 `url_path` normalizer 都是确定性的；缺值、无目标、多目标和自指不会猜测关系，而是进入带原因和候选数量的 unresolved 台账。
- Relation Build header 固定 `document-relation-plan-v1` 摘要。普通 `get_build/get_current_build` 会重读全部 relation entries、relation 实体和 unresolved 行，复核计数、确定性 ID 与摘要；升级前 digest 为空的 legacy Build 必须用新 Build ID 重建，不能继续作为 current 证据。
- OpenAI-compatible Provider 的 URL、API Key、Model 只从 Embedding Profile 指定的环境变量读取；不会写入 Profile、数据库或命令输出。
- Build 开始前执行实际 embedding probe，响应 model 和 dimensions 必须与 Profile 一致。批次响应还必须满足数量、index 顺序、有限数值和维度不漂移。
- 相同 input digest、model、dimensions 和 preprocessing version 复用同一 pgvector；完整覆盖后才将 Build 原子切换为 current/ready。
- 正式检索同时执行 pgvector 精确余弦和 PostgreSQL keyword ranking，再用 `hybrid-rrf-v1` 合并。
- 检索要求 StructuredChange 已接受、Embedding Profile 为项目当前绑定、Build 为相同 Project/Snapshot/Profile 的 current/ready；结果只返回 Canonical Slice ID、分数、通道和 query ID。Relation input、Search Index target 和 Context Profile provenance 直接联表读取 Document Convention Profile 时，也会复核 payload SHA-256 及 type/ID/semantic version envelope，不绕过 Profile 的不可变校验。
- 文档导入会写入首个 `needs_review` 状态事件。finalize 只有在 Build current/ready、eligible 覆盖率 100%、Embedding Profile 仍为当前绑定且审核数量与 Change 数量一致时，才追加新的 DocumentIngestionResult Artifact 和事件。每个 Artifact 固定自己的事件 ID；索引状态不再是 `not_started` 后还必须固定 Search Index Build ID、Embedding Profile 数据库版本 ID、binding key 和语义引用。
- 全部 Change accepted 时 Analysis Case 进入 `ready_for_impact`；仍有待审核时保持 `indexing_rag`；出现 rejected 或在 ready 后证据反转时进入 `reanalysis_required`。原始 Artifact 和旧事件不会被覆盖。
- 普通 Context Builder 继续使用 `structured-change-query-v1`。Golden 正式评测使用 `golden-cross-document-query-v2`，把已审核 required context 的文档与理由分别加入业务行为、程序契约、API 验收三类 Query；query ID 和文本仍完全确定，不依赖 AI 输出。
- Context Builder 对三类 Query 分别执行正式 Hybrid Search，trace 保留 Query purpose 和 Slice candidate ID；随后只按 ID 回查 Canonical DB，并把 Slice 证据聚合到父 Section Context Item。
- Context 与 Code Scope 加载 StructuredChange 时，同时验证不可变 Artifact SHA-256 和规范化 Change/Fact 重建结果；只有 Schema 合法但两份持久化证据不同也必须失败，不能选择任意一份继续。
- 邻域扩展只包含有界相邻 Slice 和 current/ready Relation Build 中的显式 `document_relations`；跨文档必须有显式 relation，不扫描整个 Snapshot。Context Package 记录 ingestion batch/readiness event、Search Index Build、Relation Build、Profile、Ranking、Query Planner 和完整检索参数，并把 unresolved 数量写入 unknowns。
- Token 估算包含完整 Artifact；超预算时失败并要求按 Change Group 拆分，不静默截断候选账本。相同 Context Package ID 只有在 Project、Case、Snapshot、ingestion、Change、Embedding Profile 版本/绑定、Token 预算、三类 Top-K 和邻接距离全部相同时才是完全重放；重放直接返回经 SHA-256 和数据库 envelope 复核的持久化 Artifact，不调用 Provider。
- Golden RAG expectation 有独立 Schema。只有冻结三类 Query 的 required/irrelevant Canonical ID、人工批准和质量阈值后，`require-ready` 才允许通过；silver 的 `to_be_filled` 状态不能产生伪质量结论。
- `operamind-evaluate-rag` 离线计算三类 Query 的 macro Recall@5、Recall@10、MRR、显式无关候选率和跨项目泄漏数；任何阈值失败返回非零退出码，期待值不会由 evaluator 改写。
- `operamind-run-golden-rag` 不接收人工 observed JSON，而是从已审核的 Golden expected change/context 通过固定 `golden-cross-document-query-v2` 生成三类 Query，并用当前 Profile 对真实 pgvector 与 PostgreSQL 全文索引执行检索。冻结的语义引用不会冒充物理 `node-*`：命令按已审核的 document/location 在固定 Build 中解析唯一 Canonical Slice，Report 同时保存语义引用、物理节点、Stable Key、匹配与未匹配位置、Query 文本、摘要和生成版本。同一 Snapshot／Profile／Index 最新报告缺失、失败或阻断时，Impact Report 生成和新的人工确认都会失败关闭。
- Snapshot／Profile／Build 三个参数必须一起提供或一起省略。省略时，命令遍历当前 Profile 的 ready/current Build，只接受能把全部已审核 required context 唯一解析到索引节点的一个 Build；找不到或存在多个候选时失败，不按时间或名称猜测 Scope。

## Canonical Node

当前 P2 基线把已规范化 Fact 映射为：

```text
Section (Fact Type, non-indexable)
  -> Slice (Stable Key + Canonical values, indexable)
```

Section/Slice 正文可以按 node ID 从 Canonical DB 回查。embedding input 不包含 node ID、Snapshot ID、文件名中的版本后缀或单元格位置，因此同一业务内容跨 Snapshot 可以复用向量；Project/Snapshot 隔离由 entry、node 和 build 的复合外键及查询条件保证。

## Build 命令

先完成 migration 和文档导入，再从 Canonical Fact 发布当前 Relation Build：

```bash
export OPERAMIND_DATABASE_URL='postgresql://...'

operamind-build-relations \
  --profile profiles/document-relation-profile.example.json \
  --build-id relation-build-001 \
  --project-id visiondemo \
  --snapshot-id snapshot-after \
  --profile-version-id document-relations-example@1.0.0 \
  --profile-activation-event-id relation-activation-001 \
  --activated-by reviewer@example.com \
  --activation-reason 'Reviewed document relation rules'
```

命令输出 relation 和 unresolved 数量。随后设置 Embedding Profile 声明的环境变量并构建索引：

```bash
export OPERAMIND_DATABASE_URL='postgresql://...'
export EMBED_API_URL='http://127.0.0.1:1234/v1'
export EMBED_API_KEY='lm-studio'
export EMBED_MODEL='text-embedding-nomic-embed-text-v1.5'

operamind-build-index \
  --profile profiles/embedding-profile.example.json \
  --build-id search-build-001 \
  --project-id visiondemo \
  --snapshot-id snapshot-after \
  --profile-version-id local-openai-compatible-v1@1.0.0 \
  --profile-activation-event-id embedding-activation-001 \
  --activated-by indexer@example.com \
  --activation-reason 'Build target Snapshot index'
```

命令输出 Build/Profile/Snapshot 身份、绑定的 Relation Build ID、覆盖数量、生成/复用向量数量和 current 状态，不输出 embedding、API Key 或 Canonical 正文。

Build 完成后，用导入命令输出的首事件 ID 作为 optimistic concurrency 前置条件：

```bash
operamind-finalize-rag \
  --event-id ingestion-ready-001 \
  --project-id visiondemo \
  --ingestion-batch-id ingestion-001 \
  --analysis-case-id analysis-case-001 \
  --expected-previous-event-id ingestion-event-... \
  --search-index-build-id search-build-001
```

审核结论变化后必须使用新 event ID，并把当前最新事件 ID 作为 `--expected-previous-event-id` 再次执行。命令只汇总已有证据，不调用 Embedding Provider，也不重建向量。

Analysis Case ready 后，可为一个 accepted Change 构建 Context Package。Provider 配置从数据库当前 Embedding Profile 读取，凭据仍只从 Profile 声明的环境变量读取：

```bash
operamind-build-context \
  --context-package-id context-package-001 \
  --project-id visiondemo \
  --analysis-case-id analysis-case-001 \
  --ingestion-batch-id ingestion-001 \
  --ingestion-result-event-id ingestion-ready-001 \
  --target-snapshot-id snapshot-after \
  --change-id change-001 \
  --embedding-profile-version-id local-openai-compatible-v1@1.0.0 \
  --token-budget 4000
```

离线诊断仍可读取观察结果文件：

```bash
operamind-evaluate-rag \
  --expected golden-dataset/cases/<case>/expected-rag-context.json \
  --observed artifacts/<case>-observed-rag-results.json
```

正式门禁必须直接运行当前索引：

```bash
operamind-run-golden-rag \
  --manifest golden-dataset/manifest.golden.json \
  --case-id visiondemo-expense-status-filter-golden \
  --report-id golden-rag-visiondemo-001 \
  --project-id visiondemo \
  --document-snapshot-id <committed-snapshot-id> \
  --embedding-profile-version-id <active-profile-version-id> \
  --search-index-build-id <ready-current-build-id> \
  --created-by <operator>
```

## 失败与重放

- Provider probe 失败或维度不匹配时，不创建 Build。
- Build 开始后的 Provider 失败会记录 `failed` 和无敏感信息的 failure reason；同一失败 Build ID 不允许伪装成新尝试，必须使用新 ID。
- 相同 ready Build 重放不重复生成向量。
- 新 current Build 发布时，同 Snapshot 的旧 current Build 标记为 stale。
- 新 Relation Build 发布时，同 Snapshot 且未绑定该 Relation Build 的 Search Index 标记为 stale；后续必须使用新 Build ID 重建索引。关系标签未变化时允许复用现有向量。
- Relation Build 的完全重放不重复写入；已经 stale 的旧 Build 重放不会恢复 current，也不会把旧 Relation Profile 重新激活。
- 覆盖数量不等于 eligible target 数量时，finalize 事务失败，Build 不会成为 ready/current。
- 状态事件 ID 和 Artifact ID 都是不可变身份；完全相同的 event 重放返回 `created=false`，同 ID 不同内容、Embedding binding 漂移和非最新 previous event 都失败。读取或追加事件时会对 Artifact 行加共享锁，复核规范化 JSON SHA-256，并将事件、Artifact、Snapshot Membership、Document Profile 激活审计、Search Index Build 和 Embedding Profile 激活审计交叉核对；Schema 合法但来源身份漂移同样阻断。
- 所有核心 Artifact 在读取时重新验证 Schema、规范化 JSON SHA-256、类型/版本以及 Artifact 自带的 Project/Case envelope；合法 Schema 内容被数据库内改写也会作为不可变冲突失败。
- 数据库部分唯一索引禁止同一 ingestion batch 出现两个首事件或一个 previous event 出现两个 successor；Project/Case/Artifact/Build 由复合外键隔离。
- readiness 在锁定 Analysis Case 后读取并锁定 Build、当前 Profile binding 和 Change 行，再原子写入 Artifact、事件和 Case 状态，避免并发 writer 使用同一旧状态推进。

只有确认原 worker 已退出后，才可用固定截止时间关闭中断 Build：

```bash
operamind-recover-index \
  --recovery-id search-recovery-001 \
  --build-id search-build-001 \
  --actor operator@example.com \
  --reason 'embedding worker process was interrupted' \
  --stale-before '2026-07-16T12:00:00Z'
```

未来截止时间、晚于截止时间启动的 Build、已 ready/stale 的 Build，以及同一失败 Build 上不同内容的恢复重放都会被拒绝。

## 验证

真实 PostgreSQL 18 + pgvector 0.8.2 集成测试覆盖：

- `0001-0056` 顺序升级和 checksum。
- Search Index Build 的普通读取、向量检索和关键词检索都会重算完整条目账本；条目删除、关键词漂移、向量元数据/内容漂移以及缺失版本摘要均失败关闭。迁移前的 ready/stale Build 不原地补写可信摘要，必须使用新 Build ID 重建。
- Canonical Node 与 Snapshot Membership 的事务写入/回滚。
- 二进制版本不同但 Canonical 内容相同的两个 Snapshot 只生成一个向量，第二个 Build 100% 复用。
- Provider failure 与显式 stale recovery 持久化为带唯一事件身份的 failed；精确重放幂等，冲突重放和 ready/stale 回退被阻断。
- 未接受 Change 阻断正式检索；接受后 vector + keyword 均命中相同 Canonical ID。
- 初始事件、待审核 finalize、完全相同重放、stale writer、审核通过、审核反转、Artifact 不可变和 Analysis Case 失效均在真实数据库中闭环验证。
- 三类 Query、正式 Hybrid Search、Slice rehydration、父 Section 聚合、Context Artifact 幂等持久化和预算失败回滚均有真实 PostgreSQL 闭环测试。
- 冻结 Golden Query 的真实 pgvector／全文检索、Recall@5／10、MRR、逐 Query 命中、失败／阻断报告、最新报告顺序和 Impact fail-closed 门禁均有 PostgreSQL 18 + pgvector 回归测试。
- 相邻、同文档 relation 和显式跨文档 relation 的有界扩展有独立数据库隔离测试。
- Relation Profile 精确连接、歧义/缺值 unresolved、Build 重放与 current/stale 切换，以及 Relation Build 更新导致 Search Index 失效和向量复用均有真实数据库测试。

真实 Provider 合约测试默认跳过，只有显式提供不含密钥的 Profile 路径、Profile 声明的环境变量和 live 开关时才执行。当前项目使用本机 LM Studio 的 Nomic Embed Text v1.5，Profile 固定 768 维。测试验证实际 model、维度、批量顺序、非零且不同的向量，不保存 API Key、响应正文或向量：

```bash
OPERAMIND_EMBEDDING_LIVE=1 \
OPERAMIND_EMBEDDING_LIVE_PROFILE=profiles/embedding-profile.example.json \
EMBED_API_URL='http://127.0.0.1:1234/v1' \
EMBED_API_KEY='lm-studio' \
EMBED_MODEL='text-embedding-nomic-embed-text-v1.5' \
  .venv/bin/python -m pytest -q tests/integration/test_live_embedding_provider.py
```

环境变量名以所选 Profile 的 `base_url_env`、`api_key_env`、`model_env` 为准。只有真实执行通过后才能把 live Provider 证据记为完成；测试代码存在本身不代表外部验证通过。

## 待验证与后续增强

- Golden Dataset 已冻结语义引用、已审核 expected change/context 和质量阈值；`golden-cross-document-query-v2` 的文本摘要有独立回归固定。2026-07-23 已在由三份真实 VisionDemo 设计书构建的固定 Snapshot、Nomic Embed Text v1.5 Q8 和 21/21 ready/current Index 上执行正式命令；三项 required context 均为 rank 1，Recall@5／10 和 MRR 均为 1.0，最新同 Scope 门禁实测通过。Report 还明确保留当前提取器未覆盖的辅助位置，不把部分来源覆盖隐藏为完整文档解析。
- 本地 LM Studio Nomic Provider 的 live contract test 已于 2026-07-18 实际通过，最终 Evidence 已进入 readiness manifest，`embedding_provider_live` gate 当前为 `passed`。普通自动测试仍只使用确定性 Fake，禁止隐式依赖本地服务；重新采集 Provider 证据时仍必须执行真实合约测试。
- Provider response 和 Build batch 都拒绝 model/数量/索引/维度漂移、非有限值与全零向量；Build 启动后的 cache/embedding/vector 阶段异常、publish validation 或 publish execution 异常都会尝试写入分类明确的 `failed`。只有进程强制终止或数据库本身无法记录失败时才需要显式 stale recovery，不允许把遗留 `building` 当作成功或自动接管。
- Canonical DocumentNode 的语义摘要覆盖所有进入 embedding 的 node 字段，并有意排除 source layout metadata 以保持跨版复用；结构行仍由主键、复合外键、类型约束和写入重放校验保护。读取时摘要不一致不会重新计算并接受，而是报告不可变冲突。
- 自动失败事件按 Build ID 固定身份；完全相同的失败可幂等重放，不同原因或身份会报告不可变冲突，`ready`/`stale` Build 不能倒退为失败。已有 `building` 不允许另一个调用者隐式接管或并发续跑；进程被强制终止而遗留的 Build 必须用 `operamind-recover-index` 显式关闭。恢复要求带时区且不晚于当前时间的固定 `--stale-before`、操作者和理由，恢复后使用新 Build ID 重建。
- Relation 规则目前依赖人工审阅的 Profile 和已规范化 Canonical 字段；不进行模型推断式关系发现。
- MVP 有意使用确定性的 Canonical Section/Slice 摘要聚合，避免模型遗漏事实；带事实保真校验的模型压缩属于超出当前 MVP 的后续增强，不作为 readiness 完成项。若未来引入，必须固定模型/Profile/输入输出 digest，并在事实覆盖校验失败时阻断，不能回退为未校验摘要。
