# VS Code GitHub Copilot 工作流

## 1. 职责边界

- OperaMind：文档分析、真实 RAG、Code Graph、Impact Report、批准、UI 测试和审计。
- MCP：把有界结构化结果交给 VS Code，并把修改与测试操作转交既有审计用例；不替代人类确认。
- GitHub Copilot：在当前 VS Code 工作区修改批准文件。

OperaMind 后端不远程控制 Copilot，也不把完整文档或完整仓库发送给 Copilot。

文档 Draft 和代码的最终修改只使用 VS Code 上的 GitHub Copilot，不提供 Codex CLI、任意命令 Provider 或本地 LLM 的自动执行回退。RAG Embedding 是独立边界，继续使用本地 LM Studio 的 Nomic Embed Text v1.5。

Copilot Free 停止期间可以让 OpenAI Codex 生成一次不可执行的 implementation rehearsal。它只能写 checkpoint 外部过程文件，不得修改目标 worktree；产物固定绑定 Session、Edit Packet 和 Base Revision，并强制 `executable=false`、`automatic_apply_allowed=false`。若预演发现批准约束与固定代码、测试数据矛盾，状态必须是 `needs_reanalysis` 且不包含候选修改。之后仍由 VS Code GitHub Copilot 重新读取 Packet/Grant、核对当前代码并自行修改，不能自动套用 Codex patch。

## 2. 代码修改的无文件中转（当前 POC 主路径）

代码修改不再依赖 handoff 目录或 `ai-response.json`。用户在 Web 为已绑定 ChangeSession 的 Change Request 选择隔离 linked worktree，并发布一个不可变 `CopilotCodingTask`。任务只包含 Packet、Grant、Revision、Provider 和 MCP 工具身份，不包含源码、完整设计书、Diff、测试日志或 Workspace 路径。

启用本地 Bridge：

```bash
export OPERAMIND_DATABASE_URL='postgresql:///operamind?host=/private/tmp&port=5432'
export OPERAMIND_BRIDGE_TOKEN='<random-local-secret>'
operamind-web --root . --host 127.0.0.1 --port 8765
```

安装使用的扩展由以下命令生成：

```bash
cd vscode-extension
npm ci
npm run package:vsix
```

在 VS Code Command Palette 执行 `Extensions: Install from VSIX...`，选择 `dist/operamind-copilot-bridge.vsix`。安装后打开 Web 指定的隔离 linked worktree，执行 `OperaMind: Bridge Token を安全に登録`；Token 保存在 VS Code SecretStorage。开发调试仍可按 `F5` 启动 Extension Development Host。扩展只连接 loopback URL，并以当前 Workspace 的规范化路径领取对应任务。

流程固定为：

1. Web 发布 `pending_confirmation` 任务，本地 Bridge 只发送任务通知和 ID。
2. VS Code 显示日文确认框；用户点击「確認して Copilot を開く」后，任务才进入 `accepted`。
3. 扩展打开 Copilot Chat，并要求先调用 `copilot_get_coding_task`。该工具按任务身份重新取得 Packet、Grant、Workspace/HEAD 校验结果和 Coding Plan；未确认任务不能读取。
4. Copilot 在批准路径内修改，通过 `copilot_run_task_command` 执行 Grant 白名单命令，通过 `copilot_validate_task_diff` 回传 path-only working Diff。
5. commit 后调用 `copilot_record_task_result`，把 committed path-only Diff、测试引用和摘要自动回传 Web；通过时任务进入 `completed`，越界时进入 `reanalysis_required`。

任务 claim 使用 60 秒 lease。扩展在轮询时对当前 `pending_confirmation`、`accepted` 或 `in_progress` Task 续租，并把 Task ID 保存到 Workspace State。VS Code 重启或 Bridge 短暂断线后，扩展先调用 resume，而不是发布新 Task；lease 已失效时允许同 Workspace 的新 consumer 接管并追加 `claim_recovered` Event，旧 consumer 不能再确认 Task。Bridge Client 对 network/5xx 最多重试三次，不重试认证和业务拒绝。

用户可以从日文确认框或 Command Palette 取消当前 Task；Web 也提供取消入口。取消保留完整 Event 历史。重试只能从 `cancelled`／`failed` 创建新的不可变 Task，记录 `retry_of_coding_task_id` 和递增的 `attempt_number`，并重新绑定当前 Edit Packet、Approval Grant 与 Workspace。重试不复活旧 Task，也不隐式沿用已失效 Grant。

Bridge 不发送文件，MCP 不返回 Context Package，Web 不接收源码、Diff 内容或日志正文。任务事件、命令摘要、changed path、测试引用和最终状态进入 Canonical ledger。POC 固定使用 `copilot_coding_plan + local_bridge + vscode_github_copilot`；生产 API Provider 只需实现 `coding_task_provider_v1` 并消费同一 `CopilotCodingTask`，当前尚未实现远程 Provider。

## 3. 文档 Draft 交接（保留的候选生成路径）

Draft 采用两阶段文件交接，OperaMind 不远程控制 Copilot：

1. 运行 `operamind-change-draft prepare documents ... --handoff-root <dir>`，在固定 Git Revision 的只读隔离 worktree 中生成 `COPILOT-INSTRUCTIONS.md`、`draft-prompt.json` 和响应 Schema。
2. 在 VS Code 中打开 handoff 目录，让 GitHub Copilot 按指令只写 `ai-response.json`。
3. 运行 `operamind-change-draft generate documents ... --response-file <dir>/ai-response.json --draft-root <draft-dir>`。
4. OperaMind 重新建立同一 Revision 的隔离 worktree，并校验 Schema、代码路径、测试与数据引用、UI 运行时契约和业务语义。
5. 确定性步骤自动确认；其余问题用 `next`、`answer` 分步由人确认，最后才能 `approve`。`approve` 只生成候选 Case，不授予代码写入或执行权限。

自然语言入口把 `documents` 改为 `requirement` 并传入 `--requirement`，后续边界相同。handoff 和 Draft 都是过程数据，不是 Canonical Data。

候选 Case 必须重新进入 P1-P5 主链路，形成真实混合检索的 Context Package、持久化 Code Graph、Impact Report、人类 Confirmation、Edit Packet 和 Approval Grant。P6 执行器会重新读取这些记录，并要求计划中的 Graph、Report、Confirmation、Packet 与数据库不可变 Artifact 完全一致；内存合成的“已确认”对象不能执行。

代码修改在与登记仓库共享同一 Git common-dir 的隔离 linked worktree 中完成。单独 clone 即使 origin URL 相同也不被接受。旧的 `operamind-change-loop --execute` 单体入口已移除，因为它不能把内部 Maven、Deployment 和 UI 动作逐项绑定 Canonical ledger。正式执行依次使用 Grant 约束的 `operamind-run-approved-command`、`operamind-record-edit-result`、Deployment/UI Plan 和 `operamind-ui`；OperaMind 不自行写代码。

真实项目在签发 Grant 前先用 `operamind-profile store` 保存不可变 Profile Version，再用 `operamind-profile activate` 写入项目绑定审计。VisionDemo 使用 `profiles/visiondemo-command-profile.json` 的 `visiondemo-test` 与 `visiondemo-package`；Grant 只允许本次确认范围内实际需要的 command ref。Embedding Profile 仍独立绑定本地 Nomic，不得被代码生成 Provider 替代。

`prepare` 会在 handoff 目录自动创建 `copilot-checkpoint.json`。Copilot Free 额度耗尽或模型容量不足时，使用 `operamind-copilot-checkpoint pause --reason free_quota_exhausted|model_capacity` 保存暂停原因，再用 `resume` 恢复。恢复时会重新核对 Base Revision 和 Git common-dir，不重新调用模型、不重做已完成步骤。代码修改阶段也可为隔离 worktree 创建 `code_edit` checkpoint；该模式强制要求 linked worktree、Grant ID 和 Packet ID，原仓库本身不能作为编辑 Workspace。

若额度重置晚于 Grant 到期时间，先签发同一 active Packet 的新 Grant，再在 paused checkpoint 上执行 `rebind-grant --expected-previous-grant-id <旧ID> --approval-grant-id <新ID>`。该操作只替换授权身份，并再次核对 linked worktree/Base Revision；随后显式执行 `resume`，不能借轮换扩大 Packet 文件、命令或 UI 场景范围。

额度等待期间的 Codex 预演使用：

```bash
operamind-copilot-checkpoint attach-rehearsal \
  --checkpoint-root <checkpoint-dir> \
  --proposal-file <codex-proposal.json>
operamind-copilot-checkpoint show-rehearsal \
  --checkpoint-root <checkpoint-dir>
```

`attach-rehearsal` 只接受 paused `code_edit` checkpoint、未变化的 linked worktree 和与 `expected_outputs` 完全相同的路径集合。它把经过边界校验的候选复制到 checkpoint，并生成 `COPILOT-REHEARSAL-INSTRUCTIONS.md`；整个过程前后都会复核 worktree 未变化。`needs_reanalysis` 预演会阻止该 checkpoint 恢复；替代 Report、Confirmation、Packet、Grant 形成后必须建立新的 checkpoint，不能重用旧 Packet 身份。

## 4. 当前已实现的 MCP 工具

```text
analysis_list_ready_cases
impact_get_report
copilot_get_edit_packet
copilot_get_approval_grant
copilot_run_approved_command
copilot_validate_worktree
copilot_record_edit_result
copilot_get_coding_task
copilot_run_task_command
copilot_validate_task_diff
copilot_record_task_result
verification_get_ui_plan
validation_get_result
```

前九个是兼容既有手动流程的细粒度工具；后四个是无文件 Coding Task 主路径，所有 Project/Case/Packet/Grant 范围都从 `coding_task_id` 推导，测试和 Diff 结果自动绑定任务并显示到 Web。共十三个工具。工具名只使用 VS Code 接受的 `[a-z0-9_-]` 字符，不使用点号命名空间。本地 `operamind-mcp` 按 MCP `2025-11-25` 提供 newline-delimited JSON-RPC stdio transport。它实现 initialize/initialized 生命周期、`tools/list`、`tools/call`、严格 Draft 2020-12 输入 Schema、工具属性、structuredContent、业务错误隔离和每进程最多 100 次工具调用。stdout 只输出 MCP 消息，数据库凭据只从环境变量读取。

尚待实现的 Control Plane 写入/编排 adapter 为：

```text
impact_analyze_document_changes
impact_confirm_items
verification_record_ui_result
```

六个兼容查询工具为只读、Project 范围且有界，不返回原始 Evidence 字节。`copilot_get_coding_task` 会推进任务并记录 `context_loaded` 事件，因此不是只读工具。`analysis_list_ready_cases` 最多返回 50 个非终态 Case，只接受 clean Git，并要求请求目录属于登记仓库同一 Git common-dir、origin 和阶段对应 HEAD 完全一致；Case 进入 `verifying_ui` 后使用 verified committed Edit Result 的新 HEAD，同时返回 superseded Packet 和 `ui_pending` Grant 的审计状态。

分析工具和编辑工具必须分开。没有 active Packet、有效 Grant 或匹配的 Workspace/origin/HEAD 时，`copilot_get_edit_packet` 返回工具错误。模型不能自主代替人类确认 Impact Item；未来的确认 adapter 也必须携带可审计的人类授权。

## 5. 用户流程

1. 用户在 Web 审阅影响范围、签发 Grant，并发布 Coding Task。
2. VS Code 扩展收到通知；用户在对话入口确认后打开 Copilot Coding Plan。
3. Copilot 调用 `copilot_get_coding_task`，只读取批准文件和测试文件并修改。
4. `copilot_run_task_command` 只能引用 Grant 白名单 command ref；本地执行器重新验证 Workspace/origin/HEAD 后无 shell 运行固定 argv，并回传摘要。
5. `copilot_validate_task_diff` 比较 Git path Diff 与白名单；发现额外依赖时停止并重新分析。
6. 修改 commit 后，`copilot_record_task_result` 自动绑定测试证据和新 Revision，Web 显示最终结果，再交给 OperaMind UI 验证。

## 6. Edit Packet 必须包含

- Base Repository Commit
- Editable Files 和允许符号
- Read-only Files
- Test Files
- Forbidden Globs
- 每个 Impact Item 的业务摘要和实现约束
- 必需 UI Scenario 引用
- 越界停止策略

修改阶段禁止通过 MCP 再次请求完整 Context Package。

## 7. 本地交接边界

当前仓库已经实现 `operamind-approval`、`operamind-run-approved-command`、`operamind-record-edit-result` 和不依赖额外 MCP SDK 的 stdio Server。只读工具按精确 Project/Case/Artifact 身份返回不可变 Artifact 与当前规范化状态，不返回截图、日志等原始 Evidence 内容。编辑工具只能把结构化 ID 和参数传给既有用例，不能让 Copilot 提交 shell 字符串、替换 Profile Version、重新取得 Context Package、扩大文件范围或绕过 Grant 状态。`record_edit_result` 中的测试引用必须是同一 Grant/Packet 下已经形成结果的 Command Execution；声明通过时所有引用均必须为 `passed`。

仓库的 `.vscode/mcp.json` 使用 VS Code input variable 在首次启动时安全询问 `OPERAMIND_DATABASE_URL`，不会把连接串写入 Git。先完成 editable install 和 migration，然后在 VS Code 执行 `MCP: List Servers` 启动 `operaMind`。Copilot/VS Code 自身的 Workspace Trust 和工具确认仍由客户端负责，OperaMind 不尝试绕过。

真实 GitHub Copilot 登录会话、组织 MCP Policy 和用户工具批准属于外部环境验证。自动测试已经覆盖 Extension Bridge Client、Web 路由、真实 PostgreSQL 任务状态机、claim lease 接管、取消、重试、断线续接、隔离 linked worktree、MCP Coding Plan、真实 Git Diff/commit、测试摘要和结果回传；外部 Copilot 完成会话仍必须由 readiness receipt 单独证明。额度不足、model capacity、未完成响应、缺少工具调用或工具未确认均不能作为验收通过证据。
