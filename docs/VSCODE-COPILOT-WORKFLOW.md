# VS Code GitHub Copilot 工作流

## 1. 职责边界

- OperaMind：文档分析、真实 RAG、Code Graph、Impact Report、批准、UI 测试和审计。
- MCP：把结构化结果交给 VS Code，并记录确认和修改结果。
- GitHub Copilot：在当前 VS Code 工作区修改批准文件。

OperaMind 后端不远程控制 Copilot，也不把完整文档或完整仓库发送给 Copilot。

## 2. MCP MVP 工具

```text
analysis.list_ready_cases
impact.analyze_document_changes
impact.get_report
impact.confirm_items
copilot.get_edit_packet
copilot.validate_worktree
copilot.record_edit_result
verification.get_ui_plan
verification.record_ui_result
validation.get_result
```

分析工具和编辑工具必须分开。没有 Confirmation 时，`copilot.get_edit_packet` 返回拒绝。

## 3. 用户流程

1. 用户在 Copilot Chat 请求列出当前 workspace 和 Git HEAD 对应的 ready case。
2. Copilot 调用影响分析，只展示业务变化、候选文件、证据、未知项和 UI 验证范围。
3. 用户批准明确的 Impact Item。
4. MCP 返回 Edit Packet。
5. Copilot 读取批准文件和测试文件并修改。
6. `validate_worktree` 比较 Git diff 与白名单。
7. 发现额外依赖时停止，重新分析并再次确认。
8. 修改结果绑定新 commit，交给 OperaMind UI 验证。

## 4. Edit Packet 必须包含

- Base Repository Commit
- Editable Files 和允许符号
- Read-only Files
- Test Files
- Forbidden Globs
- 每个 Impact Item 的业务摘要和实现约束
- 必需 UI Scenario 引用
- 越界停止策略

修改阶段禁止通过 MCP 再次请求完整 Context Package。
