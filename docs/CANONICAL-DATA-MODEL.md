# Canonical Data Model

## 1. 原则

- 数据库保存业务事实、版本、引用和状态。
- JSON Artifact 用于模块交换，不替代规范化查询表。
- Vector Index、缓存、压缩摘要和运行日志可重建，不成为业务事实来源。
- 每个分析结果都绑定输入版本，历史记录不可原地改写。

## 2. MVP 表组

### Project 和 Repository

```text
projects
repositories
repository_revisions
analysis_cases
```

### Profile

```text
profile_versions
project_profile_bindings
profile_activation_events
```

Profile Version 全局不可变；每次按版本或当前 Binding 回读都会重新验证 Profile Schema，并比对类型、业务 ID、语义版本、规范化 payload 与 `payload_digest`，防止合法 Schema 的内容漂移静默改变运行行为。Project Binding 保存当前指针，Activation Event 追加保存 previous/next version、操作者和原因。重复 event ID 只有内容完全一致时才视作幂等重放。

### Document

```text
documents
document_versions
document_facts
document_snapshots
snapshot_memberships
document_nodes
document_relations
document_relation_builds
document_relation_entries
document_relation_unresolved
```

P1 实现身份、版本、Snapshot Membership 和 Canonical Fact。`0024` 为每个 Document Version 增加 extractor ref，新记录固定 OperaMind adapter 与 Office parser library 版本；升级前记录显式回填为 `legacy-unversioned@0`。DocumentIngestionResult 同时固定状态事件、before/after 内容摘要、extractor ref，以及 Document Profile 的数据库版本、binding 和 activation event；解析期间文件变化会在入库前阻断。Canonical Snapshot 回读将 Fact 与 digest-validated Slice 做一一交叉校验；StructuredChange 回读将规范化 Change/Fact 行与不可变 Artifact 对账，因此任一副本缺失或漂移都不能进入正式分析。P2 索引状态 Artifact 还固定 Search Index Build 和 Embedding Profile 数据库版本/binding，并在读取时与规范化行交叉核对。P2 已增加 `document_nodes`、版本化 Relation Build、Build Entry 与 unresolved 台账。关系只能由已校验的 DocumentRelationProfile 对 Canonical 字段做确定性等值连接；无目标、多目标、自指或缺字段都记录原因，不猜测边。Context 和 embedding 只消费相同 Snapshot 的 current/ready Relation Build。当前从规范化 Fact 生成 Section/Slice，后续格式提取器可扩展 paragraph、table、row 和 cell，但检索 entry 必须始终引用 Canonical node ID。

### Search Index

```text
profile_versions (EmbeddingProfile)
search_index_builds
search_index_entries
document_search_vectors
```

Search Index Build 同时绑定生成 embedding 输入时使用的 Relation Build。发布新 Relation Build 会使同 Snapshot 的旧/进行中 Search Index stale；重建后如果 relation label 未改变，向量仍可按 input digest、model、dimensions 和 preprocessing version 复用。entry 将 Project/Snapshot/Profile/target node 绑定到缓存。检索只返回 node ID 和分数，正文从 `document_nodes` 回查。`0025` 固定失败事件 ID、失败分类、操作者、理由和可选 stale recovery 截止时间；失败记录只允许完全相同的幂等重放，已 ready/stale 的 Build 不允许回退。

### Change 和 Context

```text
structured_changes
structured_change_review_events
context_packages
```

Structured Change 保存 before/after Fact 引用和来源证据，原始 Artifact 不因人工决策而改写。Review Event 追加保存 previous event/status、接受或拒绝决策、操作者和原因；有效状态取最新事件，没有事件时取 Structured Change 的初始状态。Context Package 保存压缩事实和 ID 引用，不复制大量原文。

### Code Graph

```text
code_graph_snapshots
code_files
code_symbols
code_edges
code_test_bindings
code_graph_scan_lineage
runtime_route_evidence
runtime_route_observations
runtime_route_resolutions
unresolved_evidence_reports
unresolved_evidence_items
```

`0008` 已实现基础 Graph 规范化表，`0042` 增加 full／incremental lineage，`0045` 增加脱敏 Runtime Route Observation／Resolution 与 runtime-enriched lineage，`0046` 增加 Unresolved Evidence Report 和 item 台账。Graph Snapshot 绑定 Project、Repository、不可变 Revision、scan roots 和 Code Framework Profile；新 Snapshot 会把同 Repository 的旧 current Snapshot 标为 stale。重放必须同时匹配 Artifact、内部 Repository Revision ID、Profile Ref/Version ID 映射、失败原因和规范化计数，不能只凭外部 Profile Ref 相同复用。文件节点只保存 path、content hash、语言和角色；symbol 保存稳定签名和行号范围；edge 保存 extractor、Profile Ref、置信度、解析状态、源位置、provenance 和 Evidence Ref。resolved `tests` Edge 会派生 production/test File Binding。不保存完整源代码。

每个 usable Code Graph 自动对应一份确定性 `UnresolvedEvidenceReport`。Header 固定 Graph、Repository Revision、触发类型、Evidence Ref、open／closed 计数和可选 predecessor；item 固定原因分类、来源位置、候选、缺失 Evidence、建议及 closure proof。后继 Graph 只有在稳定 finding 对应到唯一 resolved Edge 时才关闭上一 open item；多个目标或间接名称匹配不能关闭。Report 和 normalized item 同时逐项回读校验，历史记录不更新、不删除。

### Impact 和 Edit

```text
impact_reports
impact_items
impact_confirmations
edit_packets
edit_results
command_execution_requests
command_execution_results
edit_result_command_executions
```

`0009` 已实现 Impact Report、Item 和 Confirmation。Report Artifact 保持不可变，规范化 `status` 记录 `awaiting_confirmation -> confirmed` 或新报告发布后的 `superseded`；blocked Report 将 Case 推进到 `reanalysis_required`。Confirmation 必须完整划分全部 Item，并在写入前重新验证 current complete Code Graph。

`0010` 已实现 active/superseded Edit Packet。Packet 绑定 confirmed Report、Confirmation、Repository Revision 和 Base SHA；发布前验证本地 Workspace 的 root、origin、clean HEAD 和跟踪路径，成功后 Case 进入 `editing`。

`0011` 已实现 path-only Edit Result。working/committed 两种模式均比较 Packet writable allowlist；数据库只保存 status/path/commit/test provenance，越界时 supersede Packet 并把 Case 转为 `reanalysis_required`。

`0018` 增加不可变 Approval Grant 与追加式生命周期事件。Grant 从 active Packet 派生文件、Revision、测试命令引用和 UI Scenario 白名单；过期或撤销立即阻断，成功 commit 后进入 `ui_pending` 并禁止继续编辑，首次 UI closure 后进入 `completed`。`completed` 只允许在 Revision、Packet、Scenario、Deployment 和证据来源完全一致时重做 UI 证据验证，不恢复编辑权限；边界变化仍需新审批。

`0019` 增加版本固定的安全命令执行。Grant 保存确切 `CommandExecutionProfile` Version；request 在启动前固定 Grant、Packet、Repository、Workspace、Remote、Base Revision、command ref 和模板摘要，result 只保存状态、退出码、路径、时间及输出摘要/字节数，不保存 stdout/stderr 正文。`0026` 为已预约但因进程中断未形成 Result 的执行增加显式 `interrupted` closure，固定 Recovery ID、操作者、理由和 stale boundary；不能自动重跑或覆盖已有结果。

`0020` 增加 Edit Result 到 Command Execution 的规范化证据关系。committed Result 只能引用同 Project/Case/Packet/Grant 且已经形成结果的命令；`tests_passed=true` 要求所有关联命令均为 `passed`，失败声明也不能与全通过证据矛盾。

`0021` 增加 `command_evidence_status`。升级前无法证明关联的 committed Result 标记为 `legacy_unverified` 并被 UI Plan 拒绝；working Result 为 `not_applicable`，新 committed Result 在关联数量、范围和命令状态全部一致时为 `verified`。

`0022` 隔离升级时已经存在且尚未完成的旧 UI 工作：关联 `legacy_unverified` 的 Plan 进入 `blocked`，running Run 同步关闭为 `blocked`；completed 历史记录保持不可变。

`0023` 增加 UI Plan 的 `repository_binding_status`。Plan 只有在自身 Repository Revision、committed Edit Result commit 与 Deployment Repository Revision 三者相同时才为 `verified`；升级前由字段索引错误产生的 `legacy_invalid` Plan 保留历史身份，未完成 Plan/Run 被隔离为 `blocked`，completed 历史结果不改写。

Edit Result 保存 commit、changed paths、测试引用和越界文件，不保存完整 diff。

### 测试数据执行与变更闭环

```text
test_data_execution_runs
test_data_flow_results
test_data_step_results
test_data_execution_evidence
change_closure_results
test_case_change_proposals
test_case_revisions
test_case_execution_authorizations
```

`0032` 将 `TestDataPlan` 落为追加式执行台账。每个 Run 固定 Change Orchestration、Approval Grant、Project 和 Case；按 generation flow 顺序记录 fixture、HTTP、SQL、UI 四类受限步骤及变量输出、后置断言、清理结果和脱敏 Evidence。任何步骤失败都会停止后续 setup，但仍执行已进入流程的 cleanup；运行中的记录只能通过显式结束状态关闭，不能覆盖历史结果。

`BusinessDataTemplate` 作为版本化 Artifact 保存在通用不可变 Artifact Ledger 中，可由不同 TestPlan 实例重复使用。模板在实例化前校验主从实体 DAG、主先从后的生成顺序、从先主后的删除顺序、共享变量生产者／消费者和参数前置条件。生成的 `TestDataPlan.template_instances` 只固定 Template 身份、参数名称、前置条件结果和实体顺序，不保存参数值；失败前置条件令 Plan 保持 `blocked`。

`0033` 增加不可变 `ChangeClosureResult`。关闭计算同时核对 committed 且范围内的 Edit Result、确定性命令测试、Test Data 执行和清理、100% 业务覆盖以及受影响 UI 的最终结果。证据缺失为 `blocked`，越界修改为 `reanalysis_required`，组件失败为 `failed`；只有全部适用条件通过才是 `passed`。相同 Orchestration 和组件摘要只允许内容一致的幂等重放。

`0036` 增加生成 Test Case 的自然语言修订台账。`TestCaseChangeProposal` 固定原始 Orchestration、原始 TestPlan、自然语言和跨多个 Case 的结构化操作；确定性操作也必须先展示整体差异，有歧义时在同一 Proposal 中收集全部明确选项，无法安全解释时整体阻断，不做部分修改。统一确认后生成一个不可变 `TestCaseRevision`，并在同一事务中创建新的 AcceptanceCriteria、TestPlan、TestDataPlan、BusinessCoverageReport 与 ChangeOrchestrationPlan。原 Orchestration 通过显式 supersession 边转为 `superseded`；旧 Run、执行 Artifact、Evidence、Screenshot 和 Closure 的引用写入 stale 清单，读取当前状态时不得跨 Orchestration 复用。由于修订版本共享同一已确认设计、Impact Report 和 Golden Case 摘要，`0031` 的 basis 级唯一约束在本 migration 中移除，版本唯一性改由不可变 ID、单 Proposal 单 Revision 和 supersession 状态保证。

`0047` 增加 Agent-neutral 编排任务台账。`orchestration_tasks` 保存不可变 Task Definition、Run 内单调序号与当前投影，`orchestration_task_dependencies` 保存显式依赖，`orchestration_task_claims` 保存 Executor Kind／ID、Capability、Lease Token Digest 和期限，`orchestration_task_results` 保存结果 Artifact Ref 与验收 Evidence，`orchestration_task_events` 保存完整状态历史。Agent、Subagent、人工使用同一协议；Claim 身份不替代人工确认 Artifact。成功 Result 先进入 `submitted`，只有 Canonical 业务状态前进后才进入 `completed`。每个 Run 当前最多一个活动 Task 是 Scheduler 策略，不是 Change Request 业务规则。

`0037` 增加 Test Case 改订后的执行授权台账。系统分别摘要 TestDataPlan、UI Scenario 和执行边界；边界不变时在新 Run 预约事务内追加 `reused` 记录，边界变化时必须由可信 Web 确认确切目标摘要后追加 `reconfirmed` 记录。记录固定 Revision、目标 Orchestration、Grant、新旧范围摘要、变化维度、确认者和 payload digest。已完成 Grant 只能在 Project／Case、Repository、Packet、Scenario 白名单和运行权限仍一致且未过期／撤销时复用，不恢复编辑权限。Closure 和 Web Evidence 查询均按目标 Orchestration／创建时间隔离，旧 Version 的 UI 结果不能成为新 Version 的证据。

`0038` 增加 UI Knowledge 审核截图台账。每条 Evidence 同时绑定 Observation Run、Observation、Project、source Snapshot 和业务 Target，数据库只保存受限本地 Evidence Ref、SHA-256 与脱敏标记。唯一约束保证同一 Run／Target 只有一张审核截图；复合外键阻止跨 Run、跨 Snapshot 或跨 Target 冒用。Web 查询按 result Snapshot 反查观测，图片读取时再次校验 Project、Snapshot、允许目录与文件 digest。approve/reject 继续通过追加 Review Event 生成新的不可变 Snapshot，不修改 draft 或 Evidence 历史。

`0039` 增加 Test Case Revision 撤销关系。撤销必须创建 `revision_kind=undo` 的补偿性 Revision，并通过 `undo_of_revision_id` 绑定被撤销 Revision；外键保证同 Project，唯一索引保证一个 Revision 只产生一个直接撤销。撤销把前一版本的完整 AcceptanceCriteria、TestPlan、TestDataPlan 与 Coverage 内容复制到新的不可变 Artifact ID，当前 Version 及其 Run／Evidence／Closure 转为 stale 历史。恢复后的 Version 仍需重新比较执行范围，并在可复用或重新确认的 Grant 下启动新 Run。

### UI Verification

```text
verification_scenarios
ui_environments
ui_deployments
ui_execution_plans
ui_execution_plan_scenarios
ui_preflight_checks
ui_preflight_attempts
ui_execution_runs
approval_grants
approval_grant_events
ui_execution_evidence
ui_scenario_results
change_validations
ui_browser_manifests
ui_browser_scenario_specs
ui_knowledge_snapshots
ui_knowledge_targets
ui_locator_candidates
ui_locator_observation_runs
ui_locator_observations
ui_locator_observation_evidence
ui_knowledge_review_events
```

`0012` 已实现 Scenario/Plan/Run/Result 模型；`0013` 增加 approved Browser Manifest 和逐 Scenario 的有限执行 DSL；`0014` 将五类 Preflight 归入追加式 Attempt，blocked 记录不会被覆盖，修复条件后可新建 Attempt；`0015` 增加与 Project/Environment/Deployment Revision 严格绑定的 UI Knowledge Snapshot、业务目标与带优先级/可靠性评分的 Locator candidate；`0016` 增加追加式 runtime observation run、逐候选 match/visible 计数以及 source/result Snapshot provenance；`0017` 以追加式 Review Event 把 draft 审核为新的 approved/rejected Snapshot，避免覆盖观测结果；`0038` 为一意且可见的业务目标增加元素级脱敏截图 Evidence。Plan 固定 active/approved Scenario 与 committed Edit Result 对应的 Deployment Revision；Preflight、Run 启动和非 blocked closure 都重新核对 Plan、Edit Result、Packet、Deployment、Environment 和固定 Scenario 映射。UI Knowledge 以及 Browser Manifest 的规范化 header/target/candidate/spec 会在读取时重构并复核 payload digest，防止 Locator 或执行 DSL 漂移。自动 Browser Preflight 全部通过后才能启动 Run。Evidence 只保存脱敏引用和 SHA-256，Scenario Result 与最终 `UiVerificationResult`/Case 状态在同一事务内关闭。进程中断留下的 running Run 只能通过带固定时间边界、操作人和原因的 `recover-run` 生成不可变 blocked Result，不能直接改库；Plan 和 Grant 保持可重试。

浏览器截图和大日志可以进入对象存储，但数据库必须保存不可变 Evidence Ref、Hash、环境和 Deployment Revision。

## 3. 关键唯一约束

- Snapshot 内 Document Membership 唯一。
- Snapshot 内 Stable Key 唯一；Structured Change 的 before/after Fact 必须分别属于同 Project 的 source/target Snapshot，并匹配 Stable Key 和 Fact Type。
- Structured Change Review Event 只能引用同 Project 的 Change；事件链必须引用同一 Change 的上一决策，stale writer 不能覆盖新决策。
- Project Profile Binding 在同一 `binding_key` 下只有一个当前版本，所有切换都追加 Activation Event。
- Snapshot 同时最多有一个 current Relation Build；Build Entry 和 unresolved 行都绑定不可变 Build，旧 Build 重放不能重新激活。
- Snapshot、Target ID、Embedding Profile、Ranking Policy 的 Search Entry 唯一。
- Repository Revision、Path 的 Code File 唯一。
- Impact Report、Impact Item、Confirmation 的引用不能跨 Project。
- 每个 Test Case Proposal 最多生成一个 Revision；被 supersede 的 Orchestration 只能指向同 Project 的新版本，旧执行和 Closure 只允许作为 stale 历史读取。
- 每个 Test Case 执行授权固定一个 Revision、目标 Orchestration、Grant 和目标范围摘要；`reused` 只允许范围完全不变，`reconfirmed` 只允许范围发生变化。
- UI Run 的 Deployment Revision 必须与 Edit Result 对应 Build 一致。
