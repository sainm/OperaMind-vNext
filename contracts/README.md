# Core Artifact Contracts

本目录只保存 vNext 主链路的八个核心 JSON Schema：

1. `DocumentIngestionResult`
2. `StructuredChange`
3. `ContextPackage`
4. `CodeGraphSnapshot`
5. `ImpactReport`
6. `ImpactConfirmation`
7. `CopilotEditPacket`
8. `UiVerificationResult`

Contract 用于 API、MCP、数据库 Repository、Golden Dataset 和 UI 之间的边界校验。数据库规范化表不是由这些 JSON 代替；Artifact 只提供稳定交换格式。

规则：

- 所有 Artifact 必须包含 `artifact_type` 和 `schema_version`。
- v1 默认拒绝未知字段。
- ID 只引用 Canonical Data，不把完整源代码或完整文档塞入 Artifact。
- Contract 变更必须更新 Golden Dataset，并说明兼容策略。

`examples/` 为每个 v1 Artifact 保存一个可执行示例。Baseline 校验会同时检查 Schema 和示例，避免出现“Schema 合法但没有任何真实 payload 可通过”的空契约。
