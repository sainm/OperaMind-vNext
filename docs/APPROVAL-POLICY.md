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
  "allowed_test_commands": [],
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
- Base Git SHA、Repository Remote 或 Workspace Root 不匹配。
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

命令使用参数数组和固定工作目录，不把 Copilot 生成的任意 shell 字符串视为已批准。安装依赖、修改环境、执行 migration、部署和清理共享数据默认不在普通 Edit Grant 中。

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
- 是否发生越界尝试。
- Grant 何时完成、失效或被撤销。

## 10. MVP 默认策略

- 每个 Change Group 一次业务审批。
- 每个 Grant 默认最多 3-8 个 production 文件，数量来自 Project Policy。
- 测试文件和 UI Scenario 与同一个 Grant 一起批准。
- Grant 在首次成功提交、用户撤销、Base SHA 改变或超时后失效。
- Reanalysis Request 汇总全部新增范围后只审批一次。
