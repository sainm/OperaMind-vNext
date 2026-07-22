# P4 Impact Report 与确认

## 当前完成范围

- `0009_impact_reports_and_confirmations` 增加规范化 `impact_reports`、`impact_items` 和 `impact_confirmations`，通过 Project/Case、Document Snapshot、Code Graph、Repository/Revision 复合外键固定分析边界。
- `ImpactReportService` 必须重新执行 evidence-bound Scope Resolver，不接受调用方直接提交文件白名单。报告仅由当前 ContextPackage、accepted StructuredChange、当前 Code Graph/Revision/Profile 和类型化 anchor 生成。
- 直接 anchor 命中的 production File 生成 `modify/high` Item；Profile relation 扩展到的 production File 生成 `review_only/medium` Item；显式 Test Binding 按共享 anchor 关联到生产 Item。
- ContextPackage unknown、Scope unknown、无 editable 候选、UI 影响为 unknown、或 UI impacted 但没有显式 Scenario 时，报告只能以 `blocked` 发布，Analysis Case 转为 `reanalysis_required`。
- 没有 blocking unknown 的报告以 `awaiting_confirmation` 发布，Analysis Case 转为同名状态。调用方必须显式声明 `impacted`、`not_impacted` 或 `unknown`，系统不从摘要猜测 UI 影响。
- ImpactReport Artifact 不可变，永久保存创建时的 `blocked` 或 `awaiting_confirmation`。规范化状态可以通过追加式 Confirmation 推进到 `confirmed`，或在同 Case 发布新报告时进入 `superseded`，但不会改写原 Artifact。
- Confirmation 必须一次且仅一次决定报告内全部 Item，approved/rejected 不能重叠，并至少批准一个 `modify/add/delete` Item。确认时间不得晚于数据库当前时间；确认事务以共享锁固定 Code Graph，并再次检查报告仍在等待、Graph 仍为 current 且 complete。

## 构建报告

锚点 JSON 与 `operamind-resolve-code-scope` 相同。UI 判断必须显式提供：

```bash
export OPERAMIND_DATABASE_URL='postgresql://...'

operamind-build-impact \
  --anchors scope-anchors.json \
  --impact-report-id impact-report-001 \
  --project-id visiondemo \
  --analysis-case-id analysis-case-001 \
  --context-package-id context-package-001 \
  --structured-change-id change-001 \
  --code-graph-snapshot-id code-graph-001 \
  --repository-revision-id revision-001 \
  --profile-binding-key code-framework:repository-001 \
  --ui-impact-status impacted \
  --ui-scenario-ref expense-filter-default-all \
  --planned-test-file src/test/java/example/ExpenseRepositoryIntegrationTest.java
```

`--planned-test-file` 只用于已确认 Draft 验证计划要求、且当前 Revision 尚不存在的新测试文件。它会生成独立的 `add` Impact Item，必须与其他 Item 一起接受人工确认；Packet 将其归入 `test_files`，不会把任意新生产文件伪装成测试范围。

confirmable 报告退出码为 `0`；blocked 报告仍输出完整 Artifact，但退出码为 `1`。

## 提交确认

所有 Item ID 必须完整出现在 approved 或 rejected 集合中：

```bash
operamind-confirm-impact \
  --confirmation-id confirmation-001 \
  --impact-report-id impact-report-001 \
  --project-id visiondemo \
  --analysis-case-id analysis-case-001 \
  --confirmed-by developer@example.com \
  --approved-item-id impact-item-001 \
  --user-note 'Approved the bounded change only.'
```

Confirmation 只批准 Impact 范围，不直接授予 Workspace 写权限。下一切片由 confirmed Report 生成 CopilotEditPacket，并在同一事务边界后把 Case 推进到 `editing`。

## 构建 Edit Packet

`0010_edit_packets` 保存 active/superseded Packet 的规范化白名单。Packet 只从 Confirmation 中 approved 的 Item 派生：actionable Item 进入 editable/allowed items，approved `review_only` Item 进入 read-only，测试文件只取 approved actionable Item 的 Test Binding。

Report 和 Packet 的重放不仅比较不可变 Artifact，还核对内部 Repository Revision ID、Graph ID、Item/unknown 数量、Confirmation/Repository scope 和规范化文件/Item 白名单。`EditPacketRepository.get` 是 Packet 的权威普通读取入口：它把不可变 `CopilotEditPacket` Artifact 与完整规范化 Packet 行、Repository Revision commit、同一 Report 的 `ImpactConfirmation`、Impact Item 派生的文件/符号/动作范围逐项核对。Report 后来进入 superseded 时，旧 Packet 仍可作为完整历史证据读取和重放，但不能重新成为授权来源；Grant source 只接受同时为 active Packet、confirmed Report 的组合。旧 Packet 重放返回数据库中的实际 `active`/`superseded` 状态，不把已 superseded Packet 伪报为 active。Report、Graph 和 Packet 的 current 状态在发布/授权事务中加锁，避免检查后被并发 writer 置 stale。

```bash
operamind-build-edit-packet \
  --edit-packet-id edit-packet-001 \
  --project-id visiondemo \
  --analysis-case-id analysis-case-001 \
  --impact-report-id impact-report-001 \
  --confirmation-id confirmation-001 \
  --workspace-root /absolute/path/to/target-repository \
  --forbidden-glob '**/.env' \
  --forbidden-glob '**/pom.xml' \
  --implementation-constraints constraints.json
```

发布前必须同时满足：登记 workspace root 完全一致、Git origin 一致、clean HEAD 等于 Report Base Revision、非 add 文件均存在于该 Revision、Packet 分类不重叠、无路径命中 forbidden globs。成功后 Case 才进入 `editing`。`must_not_fetch_context_package=true` 固定禁止修改阶段重新取得完整 ContextPackage。

Packet 发布后签发与其范围完全一致的 Grant。有效期必须带时区，测试命令只引用 Project Profile 中的安全模板 ID。

```bash
operamind-approval issue \
  --grant-id approval-grant-001 \
  --project-id visiondemo \
  --analysis-case-id analysis-case-001 \
  --edit-packet-id edit-packet-001 \
  --approved-by reviewer@example.com \
  --expires-at 2026-07-31T12:00:00Z \
  --command-profile-binding-key command-execution:visiondemo \
  --test-command-ref targeted-unit
```

Grant 固定签发时的 Command Profile Version。后续项目激活新版本不会改变已签发 Grant 的 argv、超时或环境白名单。Profile 回读会同时复核类型、业务 ID、语义版本、规范化 payload 和 `payload_digest`；Grant 签发及每次 inspect/authorize 还会确认白名单 command ref 确实存在于该固定版本，合法 Schema 但摘要漂移的命令模板也会被阻断。

Grant source 加载只接受 `active` Packet 和 `editing` Case，并先经过上述 Packet 权威读取；数据库中的 Packet 文件范围、allowed Items、Confirmation、Repository/Revision 或 Artifact 任一漂移都会失败关闭。签发事务再次锁定并核对 Packet、Case、confirmed Report、current/complete Graph、Confirmation 和未来有效期。Grant 的重放、inspect、编辑授权与 UI 授权也会重新验证 Packet 的不可变来源，因此不能通过只篡改规范化白名单或状态列扩大既有授权。

## 执行批准的测试命令

`0019_command_execution` 保存先于执行写入的不可变请求预约，以及追加后不可修改的摘要结果。执行器不调用 shell，不接受调用方传入任意命令字符串；stdout/stderr 只经由 pipe 流式计算摘要，不写临时文件，也不把正文保存到 PostgreSQL。每次执行使用独立进程组，超时或父进程遗留的子进程会被整体清理。

```bash
operamind-run-approved-command \
  --command-execution-id test-run-001 \
  --approval-grant-id approval-grant-001 \
  --project-id visiondemo \
  --analysis-case-id analysis-case-001 \
  --edit-packet-id edit-packet-001 \
  --workspace-root /absolute/path/to/target-repository \
  --command-ref targeted-unit
```

命令启动前重新验证 Grant 仍为 `active_editing`、包含 `run_test`、command ref 在白名单中、执行目录与 Packet 登记仓库共享同一 Git common-dir、origin/HEAD 一致，并从 Grant 固定的 Profile Version 解析模板。独立 clone 即使 origin 相同也不接受。退出码符合 Profile 时返回 `passed`；其他可审计状态为 `failed`、`timed_out` 和 `launch_failed`。完全相同的 execution ID 返回已有结果；只有预约没有结果时阻断并要求人工检查，避免未知副作用的命令被自动重跑。

`0020_edit_result_command_evidence` 将 committed Edit Result 的每个 `test_result_ref` 规范化关联到真实 Command Execution Request/Result。引用必须存在、属于同一 Project/Case/Packet/Grant，且调用方声明的 `tests_passed` 必须与所有命令状态一致，不能再用任意字符串声称测试已通过。

`0021_edit_result_evidence_state` 为升级前的结果保留显式 `legacy_unverified` 状态，不伪造命令关联；新 working 结果为 `not_applicable`，新 committed 结果只有通过范围和状态核对后才写为 `verified`。UI Plan 仅接受 `verified`，因此旧字符串证据不会静默进入浏览器验证。

`0022_quarantine_legacy_ui_plans` 在升级时把关联旧未验证证据的未完成 UI Plan 和 running Run 转为 `blocked` 并记录原因；已经完成的历史结果不改写，其源 Edit Result 仍保留 `legacy_unverified` 供审计。

## 校验工作树与记录 Edit Result

`0011_edit_results` 只保存 Git path status、changed/out-of-scope paths、Base/Result commit、测试引用与布尔结果，不保存完整 diff。working 模式要求 HEAD 仍等于 Packet Base；committed 模式要求新 HEAD、clean worktree且 Base 是新 HEAD 的祖先。rename/copy 同时核对旧、新路径，untracked 文件也纳入校验。

```bash
operamind-record-edit-result \
  --edit-result-id edit-result-working-001 \
  --edit-packet-id edit-packet-001 \
  --approval-grant-id approval-grant-001 \
  --project-id visiondemo \
  --analysis-case-id analysis-case-001 \
  --workspace-root /absolute/path/to/target-repository \
  --mode working

operamind-record-edit-result \
  --edit-result-id edit-result-committed-001 \
  --edit-packet-id edit-packet-001 \
  --approval-grant-id approval-grant-001 \
  --project-id visiondemo \
  --analysis-case-id analysis-case-001 \
  --workspace-root /absolute/path/to/target-repository \
  --mode committed \
  --test-result-ref test-run-001 \
  --tests-passed
```

任何 changed path 不属于 Packet editable/test 集合时，结果为 `out_of_scope`、Packet 立即 superseded、Case 转为 `reanalysis_required`。范围内 committed 结果在测试失败或无变化时转 `failed`；测试通过且有 UI Scenario 时转 `verifying_ui`，无 UI Scenario 时转 `passed`。

## 验证与剩余边界

PostgreSQL 集成测试覆盖 migration 空 Schema 安装、Report/Item 规范化、完整发布身份重放、Confirmation 完全决策、未来时间与规范化身份漂移阻断、Artifact 不变性、旧 Report supersede、blocked unknown、真实 Git Workspace、Packet 实际状态重放、Packet Artifact/规范化范围/Confirmation commit 篡改、只能由批准 Item 派生的文件/Item 范围、forbidden glob、dirty worktree、Grant source 对 superseded Packet 和非 editing Case 的阻断、Approval Grant Artifact/规范化行/事件摘要一致性、Grant inspect 对上游 Packet 白名单漂移的阻断、完全重放/过期/撤销/越界及编辑/UI 两阶段的上游 Packet/Report/Graph/Case/Edit Result 失效、Grant 固定且摘要一致的旧 Command Profile Version、安全命令成功/失败/超时/启动失败、预约后崩溃与显式 interrupted closure、working 结果、committed HEAD/测试证据和 Case 状态转换。

不可变 `ImpactReport` Artifact 是 Report Header 与完整 Item 账本的权威版本；普通 `get_state`、发布重放、Confirmation 以及 Edit Packet source 加载都会逐字段核对 Item 的变更引用、目标、评分、动作、理由、证据、Graph path、测试文件和 unknown。已确认 Report 还必须存在摘要校验通过且与规范化行完全一致的 `ImpactConfirmation` Artifact，并覆盖所有 Item、至少批准一个 actionable Item。任一删除或漂移均失败关闭。

`operamind-approval issue/inspect/revoke` 已实现独立 Grant Artifact、有效期和追加式撤销；每次检查都把完整规范化 Grant 行及每个生命周期事件的摘要重新绑定到不可变内容，不信任单独的状态列。`operamind-run-approved-command` 已实现 Profile 固定的本地安全命令执行与摘要审计。Grant 的命令预约、Edit Result、UI Run 创建和非 blocked UI closure 都在同一 Grant 行锁事务中重新授权，撤销或过期不能在检查与副作用之间穿透。`operamind-mcp` 已通过 stdio 暴露十三个工具：九个兼容的细粒度查询／编辑工具，以及 `copilot_get_coding_task`、`copilot_run_task_command`、`copilot_validate_task_diff`、`copilot_record_task_result` 四个无文件任务工具。Web、loopback Bridge 和 VS Code 扩展已实现发布、对话确认及测试／Diff 自动回传；分析启动和原始 UI 结果写入仍不开放为模型工具。
