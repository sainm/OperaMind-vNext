# 改修审批策略

## 1. 目标

用户在 Web 审阅并批准一次明确的改修范围后，VS Code GitHub Copilot 应在该范围内连续完成代码阅读、修改、测试和结果回写。系统不得为每个文件、每次保存、每条测试命令重复请求业务审批。

GitHub Copilot 或 VS Code 自身出于 Workspace Trust、终端命令或操作系统安全策略显示的确认不能由 OperaMind 绕过；OperaMind 负责避免额外制造重复审批。

## 2. 单次范围授权

Web 的 `Approve and Open in VS Code` 同时生成 Edit Packet 和 Approval Grant：

```json
{
  "approval_grant_id": "grant-001",
  "change_session_id": "session-001",
  "base_repository_revision": "abc123",
  "editable_files": [],
  "read_only_files": [],
  "test_files": [],
  "allowed_actions": ["read", "modify", "add_test", "run_test", "record_result"],
  "command_profile_version_id": "command-profile-version-001",
  "allowed_test_command_refs": [],
  "allowed_ui_scenarios": [],
  "forbidden_globs": [],
  "expires_at": "2026-07-15T12:00:00Z",
  "out_of_scope_policy": "collect_and_request_once"
}
```

Grant 必须绑定 Change Session、Base Git SHA、文件范围、允许动作和有效期。它不是通用仓库写权限。

Edit Packet 与 Approval Grant 的越界策略作用于不同层级，不得互相替代：

- Edit Packet 固定使用 `stop_and_reanalyze`：一旦发现范围外写入需求，立即停止任何进一步修改。
- Approval Grant 使用 `collect_and_request_once`：停止写入后仅允许继续只读分析并汇总全部缺口，最终发出一次批量 Reanalysis Request。

因此，“批量收集”不表示允许越界写入，也不允许自动扩大 Edit Packet 白名单。

## 3. 授权后无需重复审批

Grant 有效且 Git SHA 未变化时，以下动作自动允许：

- 读取 Edit Packet 中的 editable、read-only 和 test 文件。
- 修改 editable 文件中批准的符号。
- 新增或修改批准范围内的测试文件。
- 执行 Edit Packet 中预定义的 lint、typecheck、unit、integration 命令。
- 保存文件、格式化、修复范围内编译错误。
- 执行批准的 OperaMind UI Scenario。
- 记录 changed files、test result、commit 和 UI evidence。
- 在 Web 和 VS Code 之间同步 Change Session 状态。

只读 Code Graph、Impact Report、测试结果和运行日志查询不需要业务审批。

## 4. 必须重新审批的情况

以下情况暂停改修并生成一次批量 Reanalysis Request：

- 需要修改 Edit Packet 之外的 production/config/migration 文件。
- 需要删除或重命名未批准的文件、API、DB 字段或 UI 功能。
- Base Git SHA、Repository Remote 不匹配，或编辑目录不属于登记仓库同一 Git common-dir 的 linked worktree。
- 文档 Snapshot、Impact Report 或 Code Graph 已 stale。
- 需要执行未在白名单中的写数据库、部署、网络或破坏性命令。
- 发现新的 high-impact 业务行为、数据迁移或安全风险。
- UI 测试表明原影响范围明显不完整。

修改仅涉及批准文件内部的实现细节、格式化、import 或局部重构时，不重新审批。

## 5. 批量请求，不逐文件打断

Copilot 发现范围不足时先停止写入，但可以继续只读分析并收集全部缺口：

```text
missing file A
missing file B
new test file C
new migration risk D
  -> one Reanalysis Request
  -> one Web review
  -> one revised Approval Grant
```

不得每发现一个文件就弹出一次审批。

### Test Case 修订后的执行授权

自然语言修订生成新 Test Case Version 后，系统比较三个授权维度：TestDataPlan 内容、UI Scenario，以及 Repository Revision／Code Scope／执行方式／测试数据引用组成的执行范围。仅重新生成 Artifact ID 或修改不影响执行边界的非 UI 断言措辞，不视为范围变化。

- 三个维度均不变：可复用同一 Project、Case、未过期且未撤销、Scenario 白名单完全一致的 Grant；即使 Grant 已完成，也只恢复 `run_test`／`record_evidence`，不恢复代码编辑权限。首次新 Run 预约时写入 `reused` 授权记录，确认者固定为 `system:scope-unchanged`。
- 任一维度变化：开始按钮保持阻断，用户必须在可信 Web 对话中确认当前目标摘要和确切 Grant，写入 `reconfirmed` 记录后才能执行。
- Grant 过期、撤销、Project／Case 不同或 Scenario 白名单不一致：不能复用或重新确认，必须获得新的有效 Grant。

授权记录固定新旧范围摘要、变化维度、Revision、Orchestration、Grant 和确认者。旧 Run、Evidence、Screenshot 与 Closure 只能作为历史比较，不能参与新 Version 的通过判定。

## 6. 预定义命令

Project Profile 定义安全命令模板，例如：

```text
format
lint
typecheck
targeted unit test
module integration test
approved Playwright scenarios
```

`CommandExecutionProfile` 与代码扫描使用的 `CodeFrameworkProfile` 分离并独立版本化。签发 Grant 时把当前激活的 Command Profile Version 固定到 Grant；之后激活新版本不能改变已经批准的命令。

执行器只接受 Profile 中的固定参数数组，使用固定 Workspace 内工作目录、超时、显式环境变量白名单和预期退出码，并以 `shell=false` 启动绝对路径可执行文件。相对可执行文件必须解析在 Workspace 内；简单命令名只能从批准传入的 `PATH` 解析。安装依赖、修改环境、执行 migration、部署和清理共享数据默认不在普通 Edit Grant 中。

每次运行先写入不可变 `command_execution_requests` 预约，再写入唯一的 `command_execution_results`，读取时重新计算 result digest。相同 ID 和相同内容可以重放已有结果；预约存在但结果缺失时不能静默重跑。确认原进程已退出后，操作员可用 `operamind-recover-command`、固定的 timezone-aware `--stale-before`、Recovery ID、操作者和原因写入唯一 `interrupted` Result；未来边界、未过边界的预约、已有正常 Result 和冲突重放都会阻断。stdout/stderr 通过独立 pipe 流式计算摘要，不写临时文件、不保留正文；结果只保存状态、退出码、路径、时间、SHA-256、字节数和超限标记。命令运行在独立进程组中，超时会杀死整个进程组；父进程退出后仍有子进程存活时也会清理并把结果记为 failed。

```bash
operamind-recover-command \
  --recovery-id command-recovery-001 \
  --command-execution-id command-execution-001 \
  --project-id visiondemo \
  --actor operator@example.com \
  --reason 'approved command worker process was interrupted' \
  --stale-before '2026-07-16T12:00:00Z'
```

## 7. 用户体验

Web 只要求一次主要动作：

```text
[Approve and Open in VS Code]
```

VS Code 显示：

```text
Approval active
Editable files: 3
Tests: 2
UI scenarios: 3
Scope status: OK
```

改修期间只发送非阻塞状态通知。只有 Grant 失效或触发第 4 节条件时才显示阻塞审批。

## 8. 自动完成

范围内代码完成后自动执行：

```text
format/lint
  -> targeted tests
  -> worktree scope validation
  -> record edit result
  -> build/deployment handoff
  -> approved UI verification
```

成功后 Web 自动进入 UI 验证或 Passed；失败时展示失败原因和建议动作，不要求用户审批“记录失败”本身。

## 9. 审计

减少审批不等于减少审计。系统必须记录：

- 谁批准了哪个 Grant。
- Grant 对应的文档、Impact Report 和 Git SHA。
- Copilot 实际修改的文件。
- 自动执行的命令和结果。
- 命令 Profile Version、模板摘要、请求摘要和 Workspace/Revision 身份。
- 是否发生越界尝试。
- Grant 何时完成、失效或被撤销。

## 10. MVP 默认策略

- 每个 Change Group 一次业务审批。
- 每个 Grant 默认最多 3-8 个 production 文件，数量来自 Project Policy。
- 测试文件和 UI Scenario 与同一个 Grant 一起批准。
- Grant source 只接受通过完整性读取的 active Edit Packet 和 `editing` Case。Packet 读取以不可变 Artifact 为权威，逐项核对规范化文件/Item 白名单、Repository Revision commit、confirmed Impact Report 和同一 `ImpactConfirmation`；任一漂移都失败关闭，不能靠修改状态列或 JSONB 白名单扩大权限。
- Grant 在成功提交后进入 `ui_pending`，禁止继续修改代码，只允许执行原 Grant 固定的 UI Scenario；首次 UI closure 后进入 `completed`。若 Revision、Edit Packet、Scenario 集合、Deployment 和上游证据完全一致，`completed` Grant 可用于同范围 UI 证据再验证，但不能恢复编辑权限；任一范围或风险边界变化都必须重新审批。每次 inspect/authorize 都重新比对 Grant Artifact、完整规范化行、上游 Packet 完整性、生命周期事件摘要和固定 Command Profile payload 摘要。每次编辑/命令授权除锁定 Grant 外，还在同一事务中复核并锁定 active Packet、confirmed Impact Report、current/complete Code Graph 和 editing Case；任一上游身份失效都会立即阻断旧 Grant。UI 授权则要求 superseded Packet 已有同一 Grant 的 verified committed/in-scope/passing Edit Result，Report/Graph 仍有效且 Case 仍为 `verifying_ui`。Edit Result、UI Run 创建和非 blocked UI closure 都在锁定 Grant 与这些上游记录的事务中重新授权，避免撤销/过期或 stale 检查与副作用之间的竞态。用户撤销、Base SHA 改变或超时会立即失效；已启动 Run 不得在失效后发布 passed/failed closure，只能通过显式 Recovery 关闭为 blocked。
- Reanalysis Request 汇总全部新增范围后只审批一次。
